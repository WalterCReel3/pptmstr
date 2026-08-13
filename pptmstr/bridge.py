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

from .effects import Effect
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
        # Bus requests: the future to complete, and the answer to release it with
        # if the store never gets the chance to. Same sharing, same locking.
        self._requests_lock = threading.Lock()
        self._requests: dict[str, tuple[asyncio.Future[Effect], Effect]] = {}

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
        # Release everything parked before the loop dies. Without this, an agent
        # awaiting a decision at shutdown leaves a future nobody will ever complete
        # -- and an agent awaiting an inbox read is in exactly the same position,
        # so both tables are drained here rather than only the one that predates
        # the bus.
        self.fail_all_pending("shutting down")
        self.abandon_all_requests()
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

    # -- bus plumbing ------------------------------------------------------------
    #
    # A third crossing, and the one that is easiest to mistake for the second. An
    # approval is parked on a *person* and may wait hours (I8); a bus request is
    # parked on the *frame* and is answered by the store as a matter of course.
    # They are kept apart because merging them would put bus traffic in the count
    # the lost-approval watchdog reads, and "17 things await your review" would
    # start including questions nobody was ever going to be asked.

    def ask(self, request_id: str, on_abandon: Effect) -> asyncio.Future[Effect]:
        """
        Register a future for a bus request. Called from the asyncio thread.

        Must be called *before* the matching intent is emitted. Reversed, the UI
        thread can apply the intent and settle an answer that has nowhere to go,
        and the agent waits forever on a future registered a moment too late.

        ``on_abandon`` is the answer to give if the request is never applied --
        shutdown, mainly. It is supplied here rather than invented at teardown
        because only the caller knows which effect shape it is waiting for, and an
        agent released with the wrong one would crash rather than wind down.
        """
        fut: asyncio.Future[Effect] = self.loop.create_future()
        with self._requests_lock:
            self._requests[request_id] = (fut, on_abandon)
        return fut

    def settle(self, effect: Effect) -> bool:
        """
        Deliver an effect to whoever asked for it. Called from the UI thread.

        False means nobody was waiting: an effect for a request that has already
        been abandoned at shutdown, or one produced by an intent the store applied
        on its own behalf. Neither is an error.
        """
        with self._requests_lock:
            entry = self._requests.pop(effect.request_id, None)
        if entry is None:
            return False
        self.loop.call_soon_threadsafe(_settle_effect, entry[0], effect)
        return True

    def abandon_all_requests(self) -> None:
        """
        Answer every outstanding bus request with its caller's fallback.

        The counterpart to ``fail_all_pending``: an agent parked on an inbox read
        holds the window open at teardown exactly as one parked on an approval does.
        """
        with self._requests_lock:
            entries = list(self._requests.values())
            self._requests.clear()
        loop = self._loop
        if loop is None:
            return
        for fut, fallback in entries:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_settle_effect, fut, fallback)

    @property
    def asking_count(self) -> int:
        """
        Outstanding bus requests. Deliberately not folded into ``parked_count``:
        the watchdog that reads that number is looking for approvals the operator
        cannot reach, and a bus request is not one.
        """
        with self._requests_lock:
            return len(self._requests)

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


def _settle_effect(fut: asyncio.Future[Effect], effect: Effect) -> None:
    if not fut.done():
        fut.set_result(effect)


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
