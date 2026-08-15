"""
Session pool: N concurrent agents, bounded.

The ceiling is RAM, not the GIL. Every session is a Claude Code CLI subprocess, so
concurrency here buys real parallelism and costs real memory -- which is why the cap
is a number the operator sets and can see, rather than something tuned by feel and
buried (design §9).

Over-cap requests queue rather than being refused. Refusing would make the operator
babysit a launcher; queueing means "start six things" works on a box that can run
four at a time, which is the whole point of having a pool.

Lives on the asyncio thread. The UI reaches it only through Bridge.submit.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

from .bridge import Bridge
from .driver import AgentSession
from .log import LOG
from .model import NodeId


@dataclass
class SessionPool:
    """Owns every live session and the queue of ones waiting for a slot."""

    bridge: Bridge
    cap: int = 4
    sessions: dict[NodeId, AgentSession] = field(default_factory=dict)
    _running: dict[NodeId, asyncio.Task[None]] = field(default_factory=dict)
    _waiting: deque[AgentSession] = field(default_factory=deque)

    # -- queries -----------------------------------------------------------------

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def queued_count(self) -> int:
        return len(self._waiting)

    def session_for(self, node_id: NodeId) -> AgentSession | None:
        """
        The session owning a node, whether it is a root or a sub-agent.

        Sub-agents share their parent's session_id, which is what makes this a
        lookup on the first element rather than a walk up the tree.
        """
        return self.sessions.get((node_id[0], None))

    # -- lifecycle ---------------------------------------------------------------

    def submit(self, session: AgentSession) -> None:
        """
        Register a session and start it if there is room.

        The row is announced immediately either way, so a queued session is visible
        as queued rather than appearing to have been dropped.
        """
        self.sessions[session.node_id] = session
        session.announce()
        if len(self._running) < self.cap:
            self._start(session)
        else:
            self._waiting.append(session)
            LOG.info("pool", f"queued {session.task[:40]} ({len(self._waiting)} waiting)")

    def _start(self, session: AgentSession) -> None:
        task = asyncio.create_task(self._run(session))
        self._running[session.node_id] = task

    async def _run(self, session: AgentSession) -> None:
        try:
            await session.run()
        finally:
            # Freeing the slot in a finally, not after the await, is what stops a
            # crashed session from permanently consuming capacity.
            self._running.pop(session.node_id, None)
            self._drain()

    def _drain(self) -> None:
        while self._waiting and len(self._running) < self.cap:
            self._start(self._waiting.popleft())

    def set_cap(self, cap: int) -> None:
        """Raise or lower the ceiling. Raising starts queued work immediately."""
        self.cap = max(1, cap)
        self._drain()

    async def send(self, node_id: NodeId, text: str) -> None:
        """Send another prompt to a live session."""
        session = self.session_for(node_id)
        if session is not None:
            await session.send(text)

    async def close(self, node_id: NodeId) -> None:
        """
        End a session and free its slot.

        Sessions no longer end by themselves -- a finished turn leaves the client
        connected so the conversation can continue -- so closing is the only way a
        subprocess is reclaimed. That makes the cap a limit on *live* sessions
        rather than on concurrent work, and makes this a first-class action rather
        than a tidy-up.
        """
        session = self.sessions.pop(node_id, None)
        if session is not None:
            # Said before the cancel, because the session reads it while unwinding.
            # A cancelled session cannot tell a teardown someone asked for from a
            # transport dying under it, and only the first means it is finished
            # rather than broken.
            session.teardown_requested = True
        task = self._running.pop(node_id, None)
        if task is not None:
            task.cancel()
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

    async def shutdown(self) -> None:
        """
        Cancel everything and wait, so no subprocess outlives the UI.

        Quitting the application closes every session, which is the contract
        ``AgentState.DONE`` states -- and it is the one cancellation where who asked
        is known exactly. So each session is told the teardown was asked for, the
        same as ``close`` does: reporting an intended shutdown as a failure would be
        a false failure signal, which is the class of defect the terminal-state work
        exists to remove.
        """
        self._waiting.clear()
        for node_id in self._running:
            session = self.sessions.get(node_id)
            if session is not None:
                session.teardown_requested = True
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._running.clear()
