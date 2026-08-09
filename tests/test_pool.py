"""
Session pool: the concurrency cap and its queue.

The cap is subprocess-bound, so exceeding it is a real memory problem rather than a
style one. These tests use a stub session -- the point is the scheduling, and
spawning real CLI subprocesses to test a deque would be absurd.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from pptmstr.bridge import Bridge
from pptmstr.pool import SessionPool

TIMEOUT = 5.0


class StubSession:
    """Looks enough like AgentSession for the pool: announce(), run(), node_id."""

    def __init__(self, name: str, hold: asyncio.Event | None = None) -> None:
        self.session_id = name
        self.node_id = (name, None)
        self.task = name
        self.announced = False
        self.started = False
        self.finished = False
        self.interrupted = False
        self._hold = hold

    def announce(self) -> None:
        self.announced = True

    async def run(self) -> None:
        self.started = True
        try:
            if self._hold is not None:
                await self._hold.wait()
        finally:
            self.finished = True

    async def interrupt(self) -> None:
        self.interrupted = True


@pytest.fixture()
def bridge():
    b = Bridge()
    b.start()
    try:
        yield b
    finally:
        b.stop()


def run_on(bridge: Bridge, coro):
    return bridge.submit(coro).result(timeout=TIMEOUT)


def wait_until(predicate, timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_sessions_under_the_cap_all_start(bridge: Bridge) -> None:
    pool = SessionPool(bridge, cap=3)
    holds = [asyncio.Event() for _ in range(3)]
    sessions = [StubSession(f"s{i}", holds[i]) for i in range(3)]

    async def submit() -> None:
        for s in sessions:
            pool.submit(s)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: all(s.started for s in sessions))
    assert pool.running_count == 3
    assert pool.queued_count == 0

    async def release() -> None:
        for h in holds:
            h.set()

    run_on(bridge, release())


def test_over_cap_queues_rather_than_refusing(bridge: Bridge) -> None:
    """
    Refusing would make the operator babysit a launcher. "Start six things" should
    work on a box that runs four at a time.
    """
    pool = SessionPool(bridge, cap=2)
    hold = asyncio.Event()
    sessions = [StubSession(f"s{i}", hold) for i in range(5)]

    async def submit() -> None:
        for s in sessions:
            pool.submit(s)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: pool.running_count == 2)
    assert pool.queued_count == 3
    # Every session is announced immediately, so a queued one is visible as queued
    # rather than appearing to have been dropped.
    assert all(s.announced for s in sessions)
    assert sum(1 for s in sessions if s.started) == 2

    run_on(bridge, _set(hold))
    wait_until(lambda: all(s.finished for s in sessions))
    assert pool.queued_count == 0


async def _set(event: asyncio.Event) -> None:
    event.set()


def test_a_finished_session_frees_its_slot(bridge: Bridge) -> None:
    pool = SessionPool(bridge, cap=1)
    first_hold = asyncio.Event()
    first = StubSession("first", first_hold)
    second = StubSession("second")

    async def submit() -> None:
        pool.submit(first)  # type: ignore[arg-type]
        pool.submit(second)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: first.started)
    assert not second.started

    run_on(bridge, _set(first_hold))
    wait_until(lambda: second.started)


def test_a_crashed_session_frees_its_slot(bridge: Bridge) -> None:
    """
    The slot is released in a finally. Without that, one exception permanently
    reduces the pool's capacity and the cause is invisible.
    """

    class Exploding(StubSession):
        async def run(self) -> None:
            self.started = True
            raise RuntimeError("boom")

    pool = SessionPool(bridge, cap=1)
    boom = Exploding("boom")
    after = StubSession("after")

    async def submit() -> None:
        pool.submit(boom)  # type: ignore[arg-type]
        pool.submit(after)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: after.started)
    assert pool.running_count <= 1


def test_raising_the_cap_starts_queued_work(bridge: Bridge) -> None:
    pool = SessionPool(bridge, cap=1)
    hold = asyncio.Event()
    sessions = [StubSession(f"s{i}", hold) for i in range(3)]

    async def submit() -> None:
        for s in sessions:
            pool.submit(s)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: pool.running_count == 1)

    async def raise_cap() -> None:
        pool.set_cap(3)

    run_on(bridge, raise_cap())
    wait_until(lambda: all(s.started for s in sessions))
    run_on(bridge, _set(hold))


def test_cap_cannot_go_below_one(bridge: Bridge) -> None:
    """A zero cap would silently stall every future submission."""
    pool = SessionPool(bridge, cap=4)

    async def lower() -> None:
        pool.set_cap(0)

    run_on(bridge, lower())
    assert pool.cap == 1


def test_session_lookup_finds_the_owner_of_a_subagent_node(bridge: Bridge) -> None:
    """
    Sub-agents share their parent's session_id, which is what makes this a lookup
    rather than a walk up the tree.
    """
    pool = SessionPool(bridge, cap=2)
    session = StubSession("sess-1")

    async def submit() -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit())
    assert pool.session_for(("sess-1", None)) is session
    assert pool.session_for(("sess-1", "agent-a")) is session
    assert pool.session_for(("other", None)) is None


def test_shutdown_cancels_everything(bridge: Bridge) -> None:
    """No CLI subprocess may outlive the window that was supervising it."""
    pool = SessionPool(bridge, cap=2)
    hold = asyncio.Event()
    sessions = [StubSession(f"s{i}", hold) for i in range(4)]

    async def submit() -> None:
        for s in sessions:
            pool.submit(s)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: pool.running_count == 2)

    run_on(bridge, pool.shutdown())
    assert pool.running_count == 0
    assert pool.queued_count == 0
