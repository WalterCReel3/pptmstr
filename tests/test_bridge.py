"""
Bridge: the thread/asyncio boundary (I5).

These are real-thread tests, not mocks. The thing under test *is* the crossing, so
a fake loop would test nothing. Every test has a timeout, because the failure mode
of this class is hanging rather than raising, and a suite that hangs is worse than
one that fails.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from pptmstr.bridge import Bridge, Decision
from pptmstr.intents import TopicChanged
from pptmstr.model import NodeId

NODE: NodeId = ("sess-1", None)
TIMEOUT = 5.0


@pytest.fixture()
def bridge():
    b = Bridge()
    b.start()
    try:
        yield b
    finally:
        b.stop()


# -- lifecycle -----------------------------------------------------------------


def test_start_blocks_until_the_loop_is_live(bridge: Bridge) -> None:
    """
    start() must not return before the loop can accept work: a submit() racing
    startup would otherwise fail intermittently and only under load.
    """
    assert bridge.running
    assert bridge.loop.is_running()


def test_stop_is_idempotent() -> None:
    b = Bridge()
    b.start()
    b.stop()
    b.stop()  # teardown runs from both the normal and the error path
    assert not b.running


def test_double_start_is_refused() -> None:
    b = Bridge()
    b.start()
    try:
        with pytest.raises(RuntimeError):
            b.start()
    finally:
        b.stop()


def test_submit_before_start_raises() -> None:
    b = Bridge()
    with pytest.raises(RuntimeError):
        b.submit(asyncio.sleep(0))


# -- asyncio -> UI -------------------------------------------------------------


def test_emit_from_the_loop_reaches_the_ui_thread(bridge: Bridge) -> None:
    async def work() -> None:
        for i in range(3):
            bridge.emit(TopicChanged(NODE, f"step {i}"))

    bridge.submit(work()).result(timeout=TIMEOUT)
    drained = bridge.drain()
    assert [i.topic for i in drained] == ["step 0", "step 1", "step 2"]


def test_drain_preserves_order_across_threads(bridge: Bridge) -> None:
    """
    I4 says intents are applied in order. A queue that reordered would let a
    StateChanged land before the AgentSpawned that creates the node.
    """
    n = 500

    async def work() -> None:
        for i in range(n):
            bridge.emit(TopicChanged(NODE, str(i)))

    bridge.submit(work()).result(timeout=TIMEOUT)
    assert [i.topic for i in bridge.drain()] == [str(i) for i in range(n)]


def test_drain_is_empty_when_nothing_happened(bridge: Bridge) -> None:
    assert bridge.drain() == []


def test_drain_caps_and_leaves_the_rest(bridge: Bridge) -> None:
    """The cap is a livelock guard: a busy producer must not stop frames rendering."""

    async def work() -> None:
        for i in range(10):
            bridge.emit(TopicChanged(NODE, str(i)))

    bridge.submit(work()).result(timeout=TIMEOUT)
    assert len(bridge.drain(max_items=4)) == 4
    assert len(bridge.drain()) == 6


# -- UI -> asyncio -------------------------------------------------------------


def test_submit_round_trips_a_result(bridge: Bridge) -> None:
    async def compute() -> int:
        await asyncio.sleep(0)
        return 42

    assert bridge.submit(compute()).result(timeout=TIMEOUT) == 42


def test_submit_runs_on_the_loop_thread_not_the_caller(bridge: Bridge) -> None:
    """
    The whole point of the split. If SDK work ran on the UI thread, one slow await
    would stall the frame loop.
    """
    caller = threading.get_ident()

    async def where() -> int:
        return threading.get_ident()

    assert bridge.submit(where()).result(timeout=TIMEOUT) != caller


def test_submit_propagates_exceptions(bridge: Bridge) -> None:
    async def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        bridge.submit(boom()).result(timeout=TIMEOUT)


# -- approval parking: the I8 mechanism ----------------------------------------


def test_park_blocks_until_the_ui_resolves(bridge: Bridge) -> None:
    """
    The core of the approval gate: an awaited future parks one agent and nothing
    else, and the UI thread completes it.
    """
    parked = threading.Event()
    result: list[Decision] = []

    async def gate() -> None:
        fut = bridge.park("p1")
        parked.set()
        result.append(await fut)

    task = bridge.submit(gate())
    assert parked.wait(TIMEOUT)
    assert bridge.parked_count == 1

    # Still blocked: nothing has decided yet.
    assert not task.done()

    assert bridge.resolve("p1", Decision(approved=True, reason="ok"))
    task.result(timeout=TIMEOUT)
    assert result[0].approved is True
    assert result[0].reason == "ok"
    assert bridge.parked_count == 0


def test_parking_one_agent_does_not_block_another(bridge: Bridge) -> None:
    """
    I8 stated precisely: the await parks *that* agent's task only. If this failed,
    one pending approval would freeze every other agent in the app.
    """
    parked = threading.Event()

    async def blocked() -> str:
        fut = bridge.park("p1")
        parked.set()
        await fut
        return "unblocked"

    async def unaffected() -> str:
        await asyncio.sleep(0)
        return "ran anyway"

    slow = bridge.submit(blocked())
    assert parked.wait(TIMEOUT)

    assert bridge.submit(unaffected()).result(timeout=TIMEOUT) == "ran anyway"
    assert not slow.done()

    bridge.resolve("p1", Decision(approved=False))
    assert slow.result(timeout=TIMEOUT) == "unblocked"


def test_resolving_an_unknown_id_returns_false(bridge: Bridge) -> None:
    assert bridge.resolve("never-parked", Decision(approved=True)) is False


def test_double_resolve_is_refused(bridge: Bridge) -> None:
    """A double-click on approve must not be an error, and must not decide twice."""
    parked = threading.Event()

    async def gate() -> Decision:
        fut = bridge.park("p1")
        parked.set()
        return await fut

    task = bridge.submit(gate())
    assert parked.wait(TIMEOUT)

    assert bridge.resolve("p1", Decision(approved=True)) is True
    assert bridge.resolve("p1", Decision(approved=False)) is False
    assert task.result(timeout=TIMEOUT).approved is True


def test_shutdown_releases_parked_agents() -> None:
    """
    A gate awaiting a decision at shutdown would otherwise leave a future nobody
    completes, and the loop would refuse to drain. Rejecting is the safe direction:
    the tool call does not run.
    """
    b = Bridge()
    b.start()
    parked = threading.Event()
    outcome: list[Decision] = []

    async def gate() -> None:
        fut = b.park("p1")
        parked.set()
        outcome.append(await fut)

    b.submit(gate())
    assert parked.wait(TIMEOUT)

    b.stop()
    deadline = time.monotonic() + TIMEOUT
    while not outcome and time.monotonic() < deadline:
        time.sleep(0.01)

    assert outcome, "parked approval was never released at shutdown"
    assert outcome[0].approved is False


def test_many_agents_park_and_resolve_independently(bridge: Bridge) -> None:
    """Concurrency check: the pending table is touched from both threads."""
    count = 25
    all_parked = threading.Semaphore(0)

    async def gate(pid: str) -> str:
        fut = bridge.park(pid)
        all_parked.release()
        decision = await fut
        return f"{pid}:{decision.approved}"

    tasks = {pid: bridge.submit(gate(pid)) for pid in (f"p{i}" for i in range(count))}
    for _ in range(count):
        assert all_parked.acquire(timeout=TIMEOUT)
    assert bridge.parked_count == count

    for i, pid in enumerate(tasks):
        assert bridge.resolve(pid, Decision(approved=i % 2 == 0))

    got = {pid: task.result(timeout=TIMEOUT) for pid, task in tasks.items()}
    assert got == {f"p{i}": f"p{i}:{i % 2 == 0}" for i in range(count)}
    assert bridge.parked_count == 0
