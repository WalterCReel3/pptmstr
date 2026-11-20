"""
The serial worker on its own, with a ``settle`` that can be held on an event.

The writers that hold one test their own domains; these pin the loop they
share: order, the bound on ``close``, and which given-up item may still land.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from pptmstr.serialworker import AFTER_CLOSE, IN_FLIGHT, NOT_TAKEN_UP, SerialWorker


@dataclass(frozen=True)
class Done:
    key: str
    item: int


@dataclass(frozen=True)
class GivenUp:
    key: str
    item: int
    reason: str


Report = Done | GivenUp


@dataclass
class Settler:
    """
    A total ``settle`` that records what it settled, and waits on ``release``
    before settling any item in ``held``.
    """

    held: frozenset[int] = frozenset()
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    settled: list[int] = field(default_factory=list)
    extra: bool = False

    def __call__(self, key: str, item: int) -> tuple[Report, ...]:
        if item in self.held:
            self.entered.set()
            assert self.release.wait(30)
        self.settled.append(item)
        done = Done(key, item)
        return (done, done) if self.extra else (done,)


def _worker(settle: Settler) -> SerialWorker[str, int, Report]:
    return SerialWorker("test-serial", settle, GivenUp)


@pytest.fixture
def settler() -> Iterator[Settler]:
    made = Settler()
    yield made
    made.release.set()


def _thread(worker: SerialWorker[str, int, Report]) -> threading.Thread:
    thread = worker._worker
    assert thread is not None
    return thread


def _joined(worker: SerialWorker[str, int, Report]) -> None:
    thread = _thread(worker)
    thread.join(10)
    assert not thread.is_alive()


def _drained(worker: SerialWorker[str, int, Report], count: int) -> list[Report]:
    got: list[Report] = []
    deadline = time.monotonic() + 10
    while len(got) < count:
        assert time.monotonic() < deadline, got
        got.extend(worker.drain())
        time.sleep(0.001)
    return got


def test_items_are_settled_and_reported_in_the_order_they_were_submitted(
    settler: Settler,
) -> None:
    worker = _worker(settler)
    submitted = [("ab"[n % 3 == 0], n) for n in range(200)]
    for key, item in submitted:
        worker.submit(key, item)

    reports = _drained(worker, len(submitted))

    assert reports == [Done(key, item) for key, item in submitted]
    assert settler.settled == [item for _, item in submitted]
    assert _thread(worker).name == "test-serial"
    worker.close(timeout=10)


def test_drain_never_waits_on_a_settle(settler: Settler) -> None:
    settler.held = frozenset({0})
    worker = _worker(settler)
    worker.submit("a", 0)
    assert settler.entered.wait(10)

    began = time.monotonic()
    assert worker.drain() == ()
    assert time.monotonic() - began < 0.5
    assert worker.outstanding == 1

    settler.release.set()
    assert _drained(worker, 1) == [Done("a", 0)]
    assert worker.outstanding == 0


def test_close_waits_for_what_is_queued_and_returns_what_was_not_drained(
    settler: Settler,
) -> None:
    worker = _worker(settler)
    for n in range(40):
        worker.submit("a", n)

    assert worker.close(timeout=10) == tuple(Done("a", n) for n in range(40))
    assert worker.outstanding == 0
    _joined(worker)


def test_close_gives_up_at_its_bound_and_only_the_item_in_flight_may_still_land(
    settler: Settler,
) -> None:
    settler.held = frozenset({1})
    worker = _worker(settler)
    for n in range(4):
        worker.submit("a", n)
    assert settler.entered.wait(10)

    began = time.monotonic()
    reports = worker.close(timeout=0.2)
    took = time.monotonic() - began

    assert 0.15 < took < 5
    assert reports == (
        Done("a", 0),
        GivenUp("a", 1, IN_FLIGHT),
        GivenUp("a", 2, NOT_TAKEN_UP),
        GivenUp("a", 3, NOT_TAKEN_UP),
    )
    assert worker.outstanding == 0

    settler.release.set()
    _joined(worker)
    assert settler.settled == [0, 1]


def test_an_item_the_worker_never_took_up_is_not_taken_up_after_close(
    settler: Settler,
) -> None:
    """
    The worker is held before its first ``get``: nothing is in flight, so the
    oldest item is "not written" too, and the worker settles none of them once
    it is let go.
    """
    waiting, release = threading.Event(), threading.Event()

    class Held(queue.SimpleQueue[tuple[str, int] | None]):
        def get(self, block: bool = True, timeout: float | None = None) -> tuple[str, int] | None:
            waiting.set()
            assert release.wait(30)
            return super().get(block, timeout)

    worker = _worker(settler)
    worker._queue = Held()
    worker.submit("a", 0)
    worker.submit("b", 1)
    assert waiting.wait(10)

    reports = worker.close(timeout=0.05)
    release.set()
    _joined(worker)

    assert reports == (GivenUp("a", 0, NOT_TAKEN_UP), GivenUp("b", 1, NOT_TAKEN_UP))
    assert settler.settled == []


def test_a_second_close_waits_for_nothing_and_returns_only_what_came_since(
    settler: Settler,
) -> None:
    settler.held = frozenset({0})
    worker = _worker(settler)
    worker.submit("a", 0)
    assert settler.entered.wait(10)
    assert worker.close(timeout=0.05) == (GivenUp("a", 0, IN_FLIGHT),)

    began = time.monotonic()
    assert worker.close(timeout=5) == ()
    assert time.monotonic() - began < 1

    worker.submit("a", 1)
    assert worker.close(timeout=5) == (GivenUp("a", 1, AFTER_CLOSE),)


def test_an_item_submitted_after_close_is_given_up_and_never_settled(settler: Settler) -> None:
    worker = _worker(settler)
    worker.submit("a", 0)
    worker.close(timeout=10)

    worker.submit("a", 1)

    assert worker.drain() == (GivenUp("a", 1, AFTER_CLOSE),)
    assert worker.outstanding == 0
    _joined(worker)
    assert settler.settled == [0]


def test_a_worker_never_used_starts_no_thread(settler: Settler) -> None:
    worker = _worker(settler)

    assert worker.close(timeout=10) == ()
    assert worker._worker is None
    worker.submit("a", 0)
    assert worker._worker is None


def test_the_other_reports_of_a_settle_close_gave_up_on_are_lost(settler: Settler) -> None:
    """
    The current limit, pinned so no test of a writer claims otherwise: ``close``
    reports the in-flight item as given up, and whatever its settle goes on to
    report -- a board's ``Stopped``, a channel's ``Swept`` -- reaches nobody.
    """
    settler.held = frozenset({0})
    settler.extra = True
    worker = _worker(settler)
    worker.submit("a", 0)
    assert settler.entered.wait(10)

    assert worker.close(timeout=0.05) == (GivenUp("a", 0, IN_FLIGHT),)
    settler.release.set()
    _joined(worker)

    assert settler.settled == [0]
    assert worker.drain() == ()
    assert worker.close(timeout=0) == ()


def test_outstanding_counts_what_has_no_report_yet(settler: Settler) -> None:
    settler.held = frozenset({0})
    worker = _worker(settler)
    assert worker.outstanding == 0
    for n in range(3):
        worker.submit("a", n)
    assert settler.entered.wait(10)
    assert worker.outstanding == 3

    settler.release.set()
    _drained(worker, 3)
    assert worker.outstanding == 0
    worker.close(timeout=10)
