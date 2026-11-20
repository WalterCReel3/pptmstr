"""
One thread that settles items in the order they were submitted, reports each,
and closes within a bound.

It knows nothing about what an item is or what settling one does: no path, no
log, no record type. ``BoardWriter`` and ``ChannelWriter`` each hold one and
pass in what is theirs -- ``settle``, which turns one item into its reports, and
``given_up``, the one report the worker makes itself, for an item it will not
settle. It imports nothing from pptmstr.

**Order is the whole guarantee.** ``submit`` queues and returns, and the one
worker thread takes items off the queue one at a time, so items are settled in
the order they were submitted. ``drain`` takes a lock held only around list
operations, never around a settle, so it is safe to call every frame.

**``settle`` must be total, and the worker does not enforce it.** An exception
out of ``settle`` ends the thread, and every later item stays queued unreported
until ``close`` gives it up. A catch-all here would have to report something,
and what a failure means -- whether later items may still be settled past it --
is the domain's to say, so each writer guards its own IO where its reasoning
is. ``settle`` is never called with the worker's lock held, so it may take
locks of its own; none of the worker's methods may be called from inside it.

**``close`` says exactly which given-up item may still land.** The worker checks
for abandonment and marks the oldest item in flight in one critical section, and
``close`` abandons and reads that mark in another. So an item ``close`` finds not
in flight is one the worker will never settle, and is reported "not written";
only the one in flight is reported as one that may still reach disk. Whatever
that in-flight settle then reports is discarded: ``close`` has already reported
its item, and nothing collects the rest.

The worker is a daemon thread and not an executor. An executor's worker is joined
when the interpreter exits, with no bound, so a settle held by a dead network
mount would hold the exit.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable
from typing import Generic, TypeVar

K = TypeVar("K")
T = TypeVar("T")
R = TypeVar("R")

# How long ``close`` waits for queued items unless told otherwise. An item is an
# append, which takes milliseconds, so this bounds a wedged filesystem and is not
# a budget.
CLOSE_TIMEOUT = 5.0

# The reasons ``given_up`` is handed: an item submitted after ``close``, the one
# being settled when ``close`` gave up, and one the worker had not taken up.
AFTER_CLOSE = "submitted after the writer was closed"
IN_FLIGHT = (
    "its append had begun and had not returned when the writer closed; it may still reach disk"
)
NOT_TAKEN_UP = "still queued when the writer closed; not written"


class SerialWorker(Generic[K, T, R]):
    """
    ``submit`` from any thread, ``drain`` on the owner's schedule, ``close`` once
    at shutdown. ``name`` is the thread's name. ``settle(key, item)`` is every
    report one item makes, and raises nothing. ``given_up(key, item, reason)`` is
    the report for an item that was not settled, and why.
    """

    def __init__(
        self,
        name: str,
        settle: Callable[[K, T], tuple[R, ...]],
        given_up: Callable[[K, T, str], R],
    ) -> None:
        self._name = name
        self._settle = settle
        self._given_up = given_up
        self._changed = threading.Condition(threading.Lock())
        # Read and written only with ``_changed`` held.
        self._queue: queue.SimpleQueue[tuple[K, T] | None] = queue.SimpleQueue()
        self._pending: deque[tuple[K, T]] = deque()
        self._reports: list[R] = []
        self._worker: threading.Thread | None = None
        # Whether the worker is settling the oldest pending item.
        self._in_flight = False
        self._closed = False
        self._abandoned = False

    def submit(self, key: K, item: T) -> None:
        """
        Queue one item and return. After ``close`` the item is not queued, and
        its report says so.
        """
        with self._changed:
            if self._closed:
                self._reports.append(self._given_up(key, item, AFTER_CLOSE))
                return
            self._pending.append((key, item))
            self._queue.put((key, item))
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name=self._name, daemon=True)
                self._worker.start()

    def drain(self) -> tuple[R, ...]:
        """Everything reported since the last call, oldest first. Never waits."""
        with self._changed:
            settled = tuple(self._reports)
            self._reports.clear()
        return settled

    @property
    def outstanding(self) -> int:
        """How many submitted items have no report yet."""
        with self._changed:
            return len(self._pending)

    def close(self, timeout: float = CLOSE_TIMEOUT) -> tuple[R, ...]:
        """
        Stop taking items, wait at most ``timeout`` seconds for the queued ones,
        and return everything not yet drained.

        An item still queued when the wait ends is given up, and the worker
        settles nothing further. The one item already being settled is given up
        the same way, and it alone may still reach disk afterwards: its reason
        says so.

        A second call waits for nothing and returns what has been reported since,
        which is nothing unless ``submit`` was called in between.
        """
        with self._changed:
            self._closed = True
            if not self._abandoned:
                self._changed.wait_for(lambda: not self._pending, timeout=max(timeout, 0.0))
                self._abandoned = True
                for position, (key, item) in enumerate(self._pending):
                    reason = IN_FLIGHT if position == 0 and self._in_flight else NOT_TAKEN_UP
                    self._reports.append(self._given_up(key, item, reason))
                self._pending.clear()
                self._queue.put(None)
            settled = tuple(self._reports)
            self._reports.clear()
        return settled

    def _run(self) -> None:
        while (taken := self._queue.get()) is not None:
            with self._changed:
                if self._abandoned:
                    return
                self._in_flight = True
            reports = self._settle(*taken)
            with self._changed:
                if self._abandoned:
                    # ``close`` has already reported this item.
                    return
                self._in_flight = False
                self._pending.popleft()
                self._reports.extend(reports)
                self._changed.notify_all()
