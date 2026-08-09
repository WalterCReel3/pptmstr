"""
The Bridge: the only place the UI thread and the asyncio loop meet (I5).

hello_imgui's runner is a blocking main-thread loop; the Agent SDK is asyncio-native.
Neither will yield, so asyncio gets its own thread and the two communicate through
exactly two primitives:

    asyncio -> UI    a ``queue.Queue`` of intents (thread-safe by construction)
    UI -> asyncio    ``call_soon_threadsafe`` / ``run_coroutine_threadsafe``

The SDK's warning against mixing threads is about calling async code from a thread
directly. Those two functions exist precisely for this boundary and are the
supported way across it. Every crossing goes through this class; an ad-hoc one
elsewhere is a future heisenbug, and there is no way to grep for "someone touched
the loop from the wrong thread" after the fact.

Approval futures live here rather than in the store (see model.PendingApproval):
the store stays pure data and testable without an event loop, and the UI resolves
an approval by ID without ever holding an asyncio object.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import queue
import threading
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from .intents import Intent

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Decision:
    """The operator's answer to a pending approval."""

    approved: bool
    reason: str | None = None
    # Edit-then-approve (§5.3): the corrected arguments to run instead of what the
    # agent asked for. None means "run it as requested".
    edited_args: Mapping[str, Any] | None = None


class Bridge:
    """
    Owns the asyncio thread and the two crossings.

    Construct on the main thread, call ``start()`` before spawning any agent, and
    ``stop()`` during teardown.
    """

    def __init__(self) -> None:
        self.to_ui: queue.Queue[Intent] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        # Approval futures, keyed by PendingApproval.id. Written by the asyncio
        # thread when a gate parks, read and popped by the UI thread when the
        # operator decides -- genuinely shared, so genuinely locked.
        self._pending_lock = threading.Lock()
        self._pending: dict[str, asyncio.Future[Decision]] = {}

    # -- lifecycle ---------------------------------------------------------------

    def start(self, timeout: float = 5.0) -> None:
        """Start the asyncio thread and block until its loop is running."""
        if self._thread is not None:
            raise RuntimeError("bridge already started")
        self._thread = threading.Thread(target=self._run_loop, name="pptmstr-asyncio", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("asyncio loop did not start")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # Set only once the loop object exists and is installed, so any thread that
        # observes ready=True can rely on submit()/resolve() finding a live loop.
        loop.call_soon(self._ready.set)
        try:
            loop.run_forever()
        finally:
            try:
                _cancel_all(loop)
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def stop(self, timeout: float = 5.0, grace: float = 1.0) -> None:
        """
        Stop the loop and join the thread.

        Idempotent: teardown paths run from both normal exit and error handling, and
        a second stop() must not raise.

        Ordering here is load-bearing. Rejecting the parked approvals only *schedules*
        each gate's resumption; stopping the loop in the same breath would tear it
        down before any of them ran, so the agents would be hard-cancelled mid-await
        rather than seeing their denial and unwinding. The grace window lets released
        tasks reach a clean exit, and is bounded so a wedged agent cannot hold the
        application open.
        """
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        # Fail every parked approval before the loop dies. Without this, an agent
        # awaiting a decision at shutdown leaves a future nobody will ever complete.
        self.fail_all_pending("shutting down")
        try:
            asyncio.run_coroutine_threadsafe(_drain_tasks(grace), loop).result(timeout=grace + 1.0)
        except (concurrent.futures.TimeoutError, RuntimeError, concurrent.futures.CancelledError):
            # A task that will not unwind must not block shutdown; _cancel_all in the
            # loop thread's finally clause is the backstop.
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        self._thread = None
        self._loop = None
        self._ready.clear()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("bridge not started")
        return self._loop

    @property
    def running(self) -> bool:
        return self._loop is not None and self._thread is not None and self._thread.is_alive()

    # -- asyncio -> UI -----------------------------------------------------------

    def emit(self, intent: Intent) -> None:
        """
        Publish a state change to the UI thread. Safe from any thread.

        Never blocks: the queue is unbounded on purpose. Back-pressure here would
        mean the asyncio thread stalling on a slow frame, which is exactly the
        coupling the two-thread split exists to prevent.
        """
        self.to_ui.put(intent)

    def drain(self, max_items: int = 10_000) -> list[Intent]:
        """
        Take everything queued, for the UI thread to apply before it snapshots.

        The cap is a livelock guard, not a policy: a runaway producer could
        otherwise keep this loop fed forever and the frame would never render.
        Anything left over is picked up next frame.
        """
        out: list[Intent] = []
        for _ in range(max_items):
            try:
                out.append(self.to_ui.get_nowait())
            except queue.Empty:
                break
        return out

    # -- UI -> asyncio -----------------------------------------------------------

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """
        Schedule a coroutine on the loop from the UI thread.

        Returns a concurrent Future. Do not block the UI thread on it -- results come
        back as intents; the future is for cancellation and error reporting.
        """
        loop = self._loop
        if loop is None:
            # The caller already built the coroutine object, so refusing without
            # closing it leaks a "never awaited" warning at an unrelated point later.
            coro.close()
            raise RuntimeError("bridge not started")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    # -- approval plumbing -------------------------------------------------------

    def park(self, pending_id: str) -> asyncio.Future[Decision]:
        """
        Register a future for a parked tool call. Called from the asyncio thread.

        The caller awaits the returned future; the UI completes it via resolve().
        """
        fut: asyncio.Future[Decision] = self.loop.create_future()
        with self._pending_lock:
            self._pending[pending_id] = fut
        return fut

    def resolve(self, pending_id: str, decision: Decision) -> bool:
        """
        Complete a parked approval. Called from the UI thread.

        Returns False when the ID is unknown or already settled -- a double-click on
        approve, or a decision racing the agent's own cancellation. Both are normal
        and neither is an error worth raising into the frame loop.
        """
        with self._pending_lock:
            fut = self._pending.pop(pending_id, None)
        if fut is None:
            return False
        # set_result must run on the loop thread, and the future may already be
        # cancelled by the time the callback lands.
        self.loop.call_soon_threadsafe(_settle, fut, decision)
        return True

    def fail_all_pending(self, reason: str) -> None:
        """Reject every parked approval. Used on shutdown so no agent hangs."""
        with self._pending_lock:
            futures = list(self._pending.values())
            self._pending.clear()
        loop = self._loop
        if loop is None:
            return
        for fut in futures:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_settle, fut, Decision(approved=False, reason=reason))

    @property
    def parked_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)


def _settle(fut: asyncio.Future[Decision], decision: Decision) -> None:
    if not fut.done():
        fut.set_result(decision)


async def _drain_tasks(grace: float) -> None:
    """Give every other task on this loop a bounded window to finish."""
    current = asyncio.current_task()
    tasks = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if tasks:
        await asyncio.wait(tasks, timeout=grace)


def _cancel_all(loop: asyncio.AbstractEventLoop) -> None:
    tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
