"""
Session pool: N concurrent agents, bounded.

The ceiling is RAM, not the GIL. Every session is a Claude Code CLI subprocess, so
concurrency here buys real parallelism and costs real memory -- which is why the cap
is a number the operator sets and can see, rather than something tuned by feel and
buried (design §9).

Over-cap requests queue rather than being refused. Refusing would make the operator
babysit a launcher; queueing means "start six things" works on a box that can run
four at a time, which is the whole point of having a pool.

Lives on the asyncio thread. The UI reaches it only through Bridge.submit, and
by a handful of reads from the draw thread: the counts, ``session_for`` and
``has_run``. Only the last is asked a question whose answer is acted on, and it
is the one that reads under the lock the moves below are made under.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto

from .bridge import Bridge
from .driver import AgentSession
from .intents import AgentFinished
from .log import LOG
from .model import AgentState, NodeId

# How long ``shutdown`` waits for the runs it has cancelled before it cancels one a
# second time. Equal to, and not longer than, the sum of the deadlines the SDK
# puts on closing one of this application's sessions (claude_agent_sdk 0.2.164,
# read and not measured against a CLI):
#
#   5s   ``SdkMcpBridge.aclose``, once per in-process MCP server, which
#        ``Query._close_impl`` awaits ahead of the transport. A session here
#        registers one, the bus; connectors are not in-process and have no bridge.
#   20s  ``SubprocessCLITransport.close``: 5s for the write lock, then 5s each for
#        the child to exit by itself, after SIGTERM and after SIGKILL.
#
# Three awaits on that path carry no deadline and are outside the sum: the bridge
# waiting for its server task to have started, the read task after it is
# cancelled, and the stderr task after it is cancelled.
#
# A second cancel landing inside that teardown ends it where it stands: ahead of
# ``transport.close()`` nothing signals the child at all, and inside it the
# terminate/kill escalation is skipped. So a run still going at the end of this
# has used every deadline above in full or is held by something that has none,
# and the margin between those and a teardown that is merely slow is nil.
SHUTDOWN_GRACE_S = 25.0

# How long a run cancelled a second time is given before ``shutdown`` returns
# without it.
SHUTDOWN_REAP_S = 5.0


class _Found(Enum):
    """
    What ``_retire`` found under a node id.
    """

    NOTHING = auto()
    # A run, which has been cancelled.
    RUNNING = auto()
    # A session waiting for a slot.
    QUEUED = auto()
    # A session held, unannounced, behind a run that is ending.
    HELD = auto()
    # A session whose run ended without being closed: nothing takes a session
    # out of ``sessions`` when its run returns or fails by itself.
    ENDED = auto()


@dataclass
class SessionPool:
    """Owns every live session and the queue of ones waiting for a slot."""

    bridge: Bridge
    cap: int = 4
    sessions: dict[NodeId, AgentSession] = field(default_factory=dict)
    _running: dict[NodeId, asyncio.Task[None]] = field(default_factory=dict)
    _waiting: deque[AgentSession] = field(default_factory=deque)
    # Runs that have been cancelled and have not yet ended, by the node they ran
    # under. At most one per node: nothing is started under an id that has one.
    _unwinding: dict[NodeId, asyncio.Task[None]] = field(default_factory=dict)
    # The session to admit under a node id once the run unwinding there has ended.
    _succeeding: dict[NodeId, AgentSession] = field(default_factory=dict)
    # Run tasks that have been created and have not taken their first step. A
    # task cancelled while it is here never enters ``_run``, so its session never
    # runs and never reports its own end.
    _unstarted: set[asyncio.Task[None]] = field(default_factory=set)
    # Set by ``shutdown`` and never unset: from then on ``submit`` starts nothing.
    _shut: bool = False
    # Held across each move of a session from one of ``_running``,
    # ``_unwinding``, ``_succeeding`` and ``_waiting`` to another, and by
    # ``has_run``. A move is two writes -- out of one table, into the next --
    # and between them the session is in neither; ``has_run`` is asked from the
    # draw thread, and "no run" read there is what lets a session be taken off
    # the fleet. Three moves have that gap: a run retired (``_retire``), a
    # queued session started (``_drain``) and a held one admitted
    # (``_unwound``). Re-entrant, because the last calls the second. Nothing is
    # awaited with it held.
    _moving: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    shutdown_grace_s: float = SHUTDOWN_GRACE_S
    shutdown_reap_s: float = SHUTDOWN_REAP_S

    # -- queries -----------------------------------------------------------------

    @property
    def running_count(self) -> int:
        """
        Runs holding a slot. Neither this nor ``queued_count`` includes a session
        held behind a run that is ending, so for the length of that teardown a
        relaunched session shows in no count at all.
        """
        return len(self._running)

    @property
    def queued_count(self) -> int:
        return len(self._waiting)

    @property
    def shutdown_bound_s(self) -> float:
        """
        The longest ``shutdown`` waits before it returns, whatever the runs do.
        """
        return self.shutdown_grace_s + self.shutdown_reap_s

    def session_for(self, node_id: NodeId) -> AgentSession | None:
        """
        The session owning a node, whether it is a root or a sub-agent.

        Sub-agents share their parent's session_id, which is what makes this a
        lookup on the first element rather than a walk up the tree.

        Not only live ones. A session whose run ended by itself is still here
        until it is closed or replaced, with no client; and while a relaunch is
        held behind a run that is ending this is the held session, although the
        row on screen is still the ending run's.
        """
        return self.sessions.get((node_id[0], None))

    # -- lifecycle ---------------------------------------------------------------

    def submit(self, session: AgentSession) -> bool:
        """
        Register a session, and announce and start it unless a run is still ending
        under its id. False, with nothing registered, announced or started, once
        ``shutdown`` has begun: a session started then is one ``shutdown`` has
        already finished answering for.

        The row is announced whether or not there is room, so a queued session is
        visible as queued rather than appearing to have been dropped.

        A resumed session is named by the id it continues, so its ``node_id`` may
        already be held here by the session it resumes. That one is retired first,
        exactly as ``close`` would. ``sessions`` and ``_running`` have one entry
        per node, so two runs under one id means one of them is a subprocess no
        ``close`` can reach -- and the CLI has one transcript per id for them both
        to append to.

        A run that has been cancelled is not over: it goes on emitting until it has
        unwound, the last thing it says is its own end, and it says all of it under
        the node id its successor shares. The store attributes by node id and
        treats a declared end as final, so an end applied after the successor's
        announce would finish the successor on screen for good. The successor is
        therefore held, unannounced and unstarted, until that run has ended; its
        row is the earlier run's until then. Ordering the two is the only thing
        that keeps them apart, since nothing downstream can.

        The ordering is against the end of the run *task*, and three things it
        rests on are not this module's. One cancel ends a run: a run that absorbs
        its cancel holds its successor for good, and ``shutdown`` is the only
        thing here that cancels twice. What a session emits from outside its run
        -- hook callbacks and bus handlers, which the SDK runs as child tasks --
        precedes that end: ``Query._close_impl`` cancels those tasks without
        awaiting them, and their last words land first only because it goes on to
        suspend, in ``transport.close()`` at the latest (read from
        claude_agent_sdk 0.2.164, not measured against a CLI). And the held
        session costs something while it waits: it is the one ``session_for``
        returns, so ``send`` and ``interrupt`` reach a session with no client and
        do nothing, and ``close`` discards it.
        """
        node_id = session.node_id
        if self._shut:
            LOG.warn("pool", f"{node_id[0]} was submitted during shutdown; not started")
            return False
        match self._retire(node_id):
            case _Found.RUNNING:
                LOG.warn("pool", f"{node_id[0]} was still live; ended it for its relaunch")
            case _Found.QUEUED:
                LOG.info("pool", f"{node_id[0]} was queued; its relaunch replaces it")
            case _Found.HELD:
                LOG.info(
                    "pool",
                    f"{node_id[0]} was waiting for its previous run to end; "
                    "its relaunch replaces it",
                )
            case _Found.ENDED:
                LOG.info("pool", f"{node_id[0]} had ended; its relaunch replaces it")
            case _Found.NOTHING:
                pass
        self.sessions[node_id] = session
        if node_id in self._unwinding:
            self._succeeding[node_id] = session
            LOG.info("pool", f"{node_id[0]} waits for its previous run to end")
            return True
        self._admit(session)
        return True

    @property
    def _occupied(self) -> int:
        """
        Slots spoken for: live runs, and sessions held behind a run that is ending.

        A held session counts so that the slot its predecessor gave up is still
        there when it is admitted. Sessions do not end by themselves, so one that
        lost that slot to the queue would wait behind work that may never finish
        -- a live session relaunched would have become a queued one.
        """
        return len(self._running) + len(self._succeeding)

    def _admit(self, session: AgentSession) -> None:
        try:
            session.announce()
        except BaseException:
            # A session with no row is one nothing on screen can close, so it does
            # not stay findable as though it had been launched.
            if self.sessions.get(session.node_id) is session:
                del self.sessions[session.node_id]
            raise
        if self._occupied < self.cap:
            self._start(session)
        else:
            self._waiting.append(session)
            LOG.info("pool", f"queued {session.task[:40]} ({len(self._waiting)} waiting)")

    def _start(self, session: AgentSession) -> None:
        task = asyncio.create_task(self._run(session))
        self._running[session.node_id] = task
        self._unstarted.add(task)

    async def _run(self, session: AgentSession) -> None:
        # Ahead of everything, with no await before ``session.run``: from here the
        # session is entered and reports its own end, a cancelled one included.
        me = asyncio.current_task()
        if me is not None:
            self._unstarted.discard(me)
        try:
            await session.run()
        finally:
            # Freeing the slot in a finally, not after the await, is what stops a
            # crashed session from permanently consuming capacity.
            #
            # Only the slot this run holds. ``close`` gives the slot back at the
            # cancel, before the run has unwound, so by now the key may be absent
            # or belong to another run. ``submit`` starts nothing under an id whose
            # run is still ending, which is what keeps the second case from
            # arising; this check does not depend on that holding.
            if self._running.get(session.node_id) is asyncio.current_task():
                del self._running[session.node_id]
            self._drain()

    def _drain(self) -> None:
        with self._moving:
            while self._waiting and self._occupied < self.cap:
                self._start(self._waiting.popleft())

    def set_cap(self, cap: int) -> None:
        """Raise or lower the ceiling. Raising starts queued work immediately."""
        self.cap = max(1, cap)
        self._drain()

    def has_run(self, node_id: NodeId) -> bool:
        """
        Whether anything of this node's session is running, ending, or waiting
        to run: a run holding a slot, one that was cancelled and has not ended,
        a session held behind that one, or a session queued for a slot.

        Narrower than ``session_for``, which also answers with a session whose
        run ended by itself. That one has no process and nothing here will start
        one for it, so it is not a run.

        **Asked from the draw thread, and never answered "no" for a session that
        is between two of those.** A run being retired leaves ``_running`` before
        it is entered in ``_unwinding``; a held or queued session leaves its
        table before its task exists. Each such move is made with ``_moving``
        held and this reads with it held, so what is seen is the pool before a move
        or after it. The wait is for a few dict writes on the asyncio thread.
        """
        root: NodeId = (node_id[0], None)
        with self._moving:
            return (
                root in self._running
                or root in self._unwinding
                or root in self._succeeding
                or any(s.node_id == root for s in self._waiting)
            )

    async def send(self, node_id: NodeId, text: str) -> bool:
        """
        Send another prompt to a live session. True when the text was handed to
        a connected client (``AgentSession.send``), False when it was dropped.

        A session with no client drops the text with a log line and nothing else:
        no transcript entry and no intent, only the False. That is any session
        before its connect returns, one whose run has ended, and a relaunch held
        behind a run that is ending -- where the row typed into still looks live
        and the wait is the earlier subprocess's teardown, seconds and not turns.
        """
        session = self.session_for(node_id)
        if session is None:
            return False
        return await session.send(text)

    async def wake(self, node_id: NodeId, text: str) -> bool:
        """
        Send a live session pptmstr's own message (``AgentSession.wake``). True
        when a connected client took it; False, with nothing written, for a
        session with none -- closed, failed, ended, or not yet connected.
        """
        session = self.session_for(node_id)
        if session is None:
            return False
        return await session.wake(text)

    async def close(self, node_id: NodeId) -> None:
        """
        End a session and free its slot.

        Sessions no longer end by themselves -- a finished turn leaves the client
        connected so the conversation can continue -- so closing is the only way a
        subprocess is reclaimed. That makes the cap a limit on *live* sessions
        rather than on concurrent work, and makes this a first-class action rather
        than a tidy-up.

        The slot is free at the cancel and not when the run has unwound, so queued
        work starts while the closed subprocess is still being reaped and
        ``running_count`` does not count it: for the length of a teardown the
        number of live subprocesses can exceed the cap by one per close in flight.

        A run is cancelled once and then cannot be reached from here again. While
        a relaunch is held behind a run that is ending, the row is the ending
        run's and the session under its id is the relaunch, so a second close on
        that row discards the relaunch: it is never announced, and the log line
        below is the only thing that says so.
        """
        if self._retire(node_id) is _Found.HELD:
            LOG.warn(
                "pool",
                f"{node_id[0]} was closed while its relaunch waited for the "
                "previous run to end; the relaunch is discarded",
            )
        self._drain()

    def _retire(self, node_id: NodeId) -> _Found:
        """
        Take a node's session out of every structure here and cancel its run.

        Says what there was. Does not drain: ``submit`` retires a node in order to
        register its successor, and the slot is the successor's to take.
        """
        with self._moving:
            return self._retired(node_id)

    def _retired(self, node_id: NodeId) -> _Found:
        found = _Found.NOTHING
        session = self.sessions.pop(node_id, None)
        if session is not None:
            # Said before the cancel, because the session reads it while unwinding.
            # A cancelled session cannot tell a teardown someone asked for from a
            # transport dying under it, and only the first means it is finished
            # rather than broken.
            session.teardown_requested = True
            # A session still waiting for a slot has no task to cancel, and left in
            # the queue it would be started later with nothing able to find it.
            # By identity, since the question is which object is queued and not
            # whether two sessions compare alike.
            found = _Found.ENDED
            queued = any(s is session for s in self._waiting)
            if queued:
                found = _Found.QUEUED
                self._waiting = deque(s for s in self._waiting if s is not session)
                self._end_row(node_id)
            # One held behind an ending run was never announced, so there is no row
            # of its own to end: the run it was waiting on ends the one they share.
            if self._succeeding.get(node_id) is session:
                found = _Found.HELD
                del self._succeeding[node_id]
        task = self._running.pop(node_id, None)
        if task is not None:
            found = _Found.RUNNING
            if task in self._unstarted:
                # Cancelled before its first step, a task ends without entering
                # its coroutine. Started and queued are the two ways to be
                # announced and never run, and a session is in one or the other,
                # which is what keeps this from saying a queued one's end twice.
                self._unstarted.discard(task)
                self._end_row(node_id)
            task.cancel()
            # Kept until it has ended, which is what ``submit`` and ``shutdown``
            # wait on. A done callback and not ``_run``'s finally: a run cancelled
            # before its first step never enters the coroutine, so its finally
            # never executes, and a callback fires for every way a task can end.
            self._unwinding[node_id] = task
            task.add_done_callback(lambda ended: self._unwound(node_id, ended))
        return found

    def _end_row(self, node_id: NodeId) -> None:
        """
        Say that an announced session which will never run is over.

        A session that runs reports its own end while it unwinds. One that never
        ran has nothing else to say its announced row is over, and SPAWNING is a
        state that holds the render loop awake.
        """
        self.bridge.emit(AgentFinished(node_id, AgentState.DONE, time.monotonic()))

    def _unwound(self, node_id: NodeId, task: asyncio.Task[None]) -> None:
        """A cancelled run has ended: admit whatever was held behind it."""
        with self._moving:
            self._admit_successor(node_id, task)

    def _admit_successor(self, node_id: NodeId, task: asyncio.Task[None]) -> None:
        if self._unwinding.get(node_id) is task:
            del self._unwinding[node_id]
        successor = self._succeeding.pop(node_id, None)
        if successor is None:
            return
        try:
            self._admit(successor)
        except Exception as exc:  # noqa: BLE001 - there is no caller to raise to
            # A done callback's exception goes to the loop's handler and nowhere
            # else, and the launch was reported as started when it was held, so
            # this line is the only place its failure is said.
            LOG.error(
                "pool",
                f"{node_id[0]} could not be started once its previous run had "
                f"ended: {type(exc).__name__}: {exc}",
            )
            # The slot kept for it is nobody's now.
            self._drain()

    async def interrupt(self, node_id: NodeId) -> None:
        """
        Stop a session's current work without ending the session.

        The recoverable lever of the three in §9 -- interrupt keeps the session and
        its context, disconnect ends it, killing the subprocess loses both.
        """
        session = self.session_for(node_id)
        if session is not None:
            await session.interrupt()

    async def shutdown(self) -> tuple[NodeId, ...]:
        """
        Cancel everything and wait, so no subprocess outlives the UI. Returns the
        nodes whose runs had still not ended when it stopped waiting, which is
        none of them unless something is wrong.

        Nothing is started from the moment this is called: what is queued or held
        is dropped, and ``submit`` refuses from then on.

        Waited on with the rest are the runs ``close`` has already cancelled and
        that have not ended: their subprocesses are as live as any other. They are
        not cancelled a second time while their teardown may still be under way,
        which would interrupt it.

        The wait is bounded, because one run that will not end would otherwise
        keep the application from quitting. A run still going after
        ``shutdown_grace_s`` is named in the log and cancelled again. By then the
        deadlines the SDK puts on closing a session have run out -- see
        ``SHUTDOWN_GRACE_S`` for how little is to spare -- so a teardown on
        schedule is not what the second cancel interrupts, and a run that
        absorbed its first is ended by it as an ordinary close. One still going
        ``shutdown_reap_s`` after that is named again and left: its subprocess is
        then the SDK's exit-time reaper's to end.

        **An empty tuple says every run task ended, not that every subprocess was
        reaped.** A second cancel that lands inside the SDK's teardown ends the
        task there, with the child unsignalled or signalled and unawaited. That
        run is not in the tuple, and its child is the exit-time reaper's as well.

        **No end is emitted here for a session that never ran.** That is a run
        cancelled before its first step, which never enters its coroutine, and a
        session dropped from the queue; both were announced, and their rows stay
        SPAWNING, a state that holds the render loop awake. ``close`` says those
        ends through ``_end_row`` and this does not. A session dropped while held
        was never announced and has no row. Nothing reads the difference while
        the only caller is ``app._shut_down``, which runs once the frame loop has
        returned; a caller with a frame loop still drawing has to end those rows
        itself.

        Quitting the application closes every session, which is the contract
        ``AgentState.DONE`` states -- and it is the one cancellation where who asked
        is known exactly. So each session is told the teardown was asked for, the
        same as ``close`` does: reporting an intended shutdown as a failure would be
        a false failure signal, which is the class of defect the terminal-state work
        exists to remove.
        """
        self._shut = True
        self._waiting.clear()
        # Before the wait: a run ending during it would otherwise admit its
        # successor into a pool that is shutting down.
        self._succeeding.clear()
        for node_id in self._running:
            session = self.sessions.get(node_id)
            if session is not None:
                session.teardown_requested = True
        ending = {task: node_id for node_id, task in self._running.items()}
        for task in ending:
            task.cancel()
        ending.update({task: node_id for node_id, task in self._unwinding.items()})
        left = await _outlasting(set(ending), self.shutdown_grace_s)
        for task in left:
            LOG.error(
                "pool",
                f"{ending[task][0]} had not ended {self.shutdown_grace_s:g}s after "
                "it was cancelled; cancelling it again",
            )
            task.cancel()
        left = await _outlasting(left, self.shutdown_reap_s)
        for task in left:
            LOG.error(
                "pool",
                f"{ending[task][0]} did not end when cancelled a second time; "
                "shutdown is not waiting for it",
            )
        self._running.clear()
        self._unstarted.clear()
        return tuple(ending[task] for task in left)


async def _outlasting(tasks: set[asyncio.Task[None]], timeout: float) -> set[asyncio.Task[None]]:
    """
    Wait for tasks to end, for at most ``timeout``; the ones that have not.

    Raises nothing of theirs. What an ended one raised is read, so that asyncio
    does not report it as an exception nobody retrieved.
    """
    if not tasks:
        return set()
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in done:
        if not task.cancelled():
            task.exception()
    return pending
