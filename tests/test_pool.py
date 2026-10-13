"""
Session pool: the concurrency cap and its queue.

The cap is subprocess-bound, so exceeding it is a real memory problem rather than a
style one. These tests use a stub session -- the point is the scheduling, and
spawning real CLI subprocesses to test a deque would be absurd.
"""

from __future__ import annotations

import asyncio
import gc
import threading
import time
from collections import deque
from pathlib import Path

import pytest

from pptmstr.bridge import Bridge
from pptmstr.intents import AgentFinished, AgentSpawned
from pptmstr.log import LOG, Level
from pptmstr.model import AgentState
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
        self.sent: list[str] = []
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

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.takes

    # Whether ``send`` hands the text to a client, as ``AgentSession.send`` answers.
    takes = True


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


def pool_log_since(mark: int) -> list[tuple[Level, str]]:
    """
    What the pool has logged since ``mark``, which is ``LOG.snapshot()[1]`` taken
    earlier: the revision counts appends, and the ring is far longer than a test.
    """
    entries, revision = LOG.snapshot()
    recent = entries[len(entries) - (revision - mark) :] if revision > mark else []
    return [(e.level, e.text) for e in recent if e.source == "pool"]


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


# -- conversational sessions ---------------------------------------------------


def test_close_ends_a_session_and_frees_its_slot(bridge: Bridge) -> None:
    """
    Sessions no longer end by themselves, so closing is the only way a subprocess
    is reclaimed. That makes the cap a limit on live sessions rather than on
    concurrent work, and makes close a first-class action.
    """
    pool = SessionPool(bridge, cap=1)
    hold = asyncio.Event()
    first = StubSession("first", hold)
    second = StubSession("second", asyncio.Event())

    async def submit() -> None:
        pool.submit(first)  # type: ignore[arg-type]
        pool.submit(second)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: first.started)
    assert not second.started

    run_on(bridge, pool.close(first.node_id))
    wait_until(lambda: second.started)
    assert first.node_id not in pool.sessions


def test_closing_an_unknown_node_is_harmless(bridge: Bridge) -> None:
    """The UI can ask twice, or ask about a node that just went away."""
    pool = SessionPool(bridge, cap=2)
    run_on(bridge, pool.close(("nope", None)))


def test_send_reaches_the_owning_session(bridge: Bridge) -> None:
    pool = SessionPool(bridge, cap=2)
    session = StubSession("sess-1", asyncio.Event())

    async def submit() -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit())
    run_on(bridge, pool.send(("sess-1", None), "another turn"))
    assert session.sent == ["another turn"]


def test_send_from_a_subagent_node_reaches_the_root_session(bridge: Bridge) -> None:
    """Sub-agents have no input channel; a message routes to the owning session."""
    pool = SessionPool(bridge, cap=2)
    session = StubSession("sess-1", asyncio.Event())

    async def submit() -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit())
    run_on(bridge, pool.send(("sess-1", "agent-a"), "hello"))
    assert session.sent == ["hello"]


# -- one node id, two sessions -------------------------------------------------
#
# A resumed session is named by the id it continues, so it has the node id of the
# session it replaces. Everything the pool keys on that id is then shared between
# a run that is ending and a run that is starting.


class SlowUnwindSession(StubSession):
    """
    A session whose ``run`` does not return on the turn it is cancelled.

    The real one disconnects a client and reaps a subprocess on the way out, so the
    interval between the cancel and the pool's ``finally`` is not empty. ``unwound``
    holds it open for as long as a test needs something else to happen inside it.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name, asyncio.Event())
        self.unwound = asyncio.Event()
        self.cancelled = False
        self.teardown_requested = False

    async def run(self) -> None:
        self.started = True
        assert self._hold is not None
        try:
            await self._hold.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            await self.unwound.wait()
            raise
        finally:
            self.finished = True


def live_run_tasks(bridge: Bridge) -> int:
    """How many ``SessionPool._run`` tasks the loop is still carrying."""

    async def count() -> int:
        return sum(
            1
            for task in asyncio.all_tasks()
            if getattr(task.get_coro(), "__qualname__", "") == "SessionPool._run"
        )

    live: int = run_on(bridge, count())
    return live


def forget_unwinding(pool: SessionPool) -> None:
    """
    Make the pool lose track of the runs it has cancelled.

    Call on the loop thread. A session submitted afterwards under one of their ids
    is announced and started at once, beside a run that has not ended.
    """
    pool._unwinding.clear()


def test_a_run_still_unwinding_does_not_free_the_slot_of_its_replacement(
    bridge: Bridge,
) -> None:
    """
    Close, then resume the same id before the closed run has finished unwinding.

    The closed run's ``finally`` executes after the replacement is registered under
    the same node id. It may only give back a slot it still holds: taking the
    replacement's leaves a live session the pool does not count and ``close``
    cannot reach, which is a subprocess nothing reclaims.

    The pool holds a relaunch until the closed run has ended, so this interleaving
    is not one a caller can produce. ``forget_unwinding`` puts it back: what is
    under test is the ownership check with the ordering in front of it taken away.
    """
    pool = SessionPool(bridge, cap=2)
    old = SlowUnwindSession("resumed")
    new = SlowUnwindSession("resumed")

    async def submit_old() -> None:
        pool.submit(old)  # type: ignore[arg-type]

    run_on(bridge, submit_old())
    wait_until(lambda: old.started)

    async def close_then_relaunch() -> None:
        await pool.close(old.node_id)
        forget_unwinding(pool)
        pool.submit(new)  # type: ignore[arg-type]

    run_on(bridge, close_then_relaunch())
    wait_until(lambda: old.cancelled and new.started)
    assert not old.finished

    run_on(bridge, _set(old.unwound))
    wait_until(lambda: old.finished)

    # A round trip through the loop, so the closed run's ``finally`` is behind it.
    assert live_run_tasks(bridge) == 1
    assert pool.running_count == 1
    assert pool.session_for(new.node_id) is new  # type: ignore[comparison-overlap]

    run_on(bridge, pool.close(new.node_id))
    wait_until(lambda: new.cancelled)
    assert new.teardown_requested
    run_on(bridge, _set(new.unwound))
    wait_until(lambda: live_run_tasks(bridge) == 0)
    assert pool.running_count == 0


def test_a_run_still_unwinding_does_not_start_queued_work_over_the_cap(
    bridge: Bridge,
) -> None:
    """
    The same interleaving at a full pool. A slot wrongly given back is taken by
    whatever is waiting, and the cap is a memory bound rather than a preference.
    Reached the same way, with the ordering taken away.
    """
    pool = SessionPool(bridge, cap=1)
    old = SlowUnwindSession("resumed")
    new = SlowUnwindSession("resumed")
    queued = SlowUnwindSession("queued")

    async def submit_old() -> None:
        pool.submit(old)  # type: ignore[arg-type]

    run_on(bridge, submit_old())
    wait_until(lambda: old.started)

    async def close_then_relaunch() -> None:
        await pool.close(old.node_id)
        forget_unwinding(pool)
        pool.submit(new)  # type: ignore[arg-type]
        pool.submit(queued)  # type: ignore[arg-type]

    run_on(bridge, close_then_relaunch())
    wait_until(lambda: old.cancelled and new.started)
    run_on(bridge, _set(old.unwound))
    wait_until(lambda: old.finished)

    # A round trip through the loop, so the closed run's ``finally`` has drained.
    assert live_run_tasks(bridge) == 1
    assert not queued.started
    assert pool.running_count == 1
    assert pool.queued_count == 1

    # Set first: ``shutdown`` waits for the runs it cancels.
    run_on(bridge, _set(new.unwound))
    run_on(bridge, pool.shutdown())


def test_a_session_submitted_over_a_live_one_with_its_id_ends_the_live_one(
    bridge: Bridge,
) -> None:
    """
    Resume an id whose session was never closed.

    Two runs under one node id cannot both be reachable: ``close`` finds one of
    them. The pool ends the earlier one as a requested teardown, so every run task
    left alive is one it counts and can cancel.
    """
    pool = SessionPool(bridge, cap=2)
    old = SlowUnwindSession("resumed")
    new = SlowUnwindSession("resumed")

    async def submit(session: SlowUnwindSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(old))
    wait_until(lambda: old.started)
    run_on(bridge, submit(new))

    wait_until(lambda: old.cancelled)
    assert old.teardown_requested
    assert not new.teardown_requested
    run_on(bridge, _set(old.unwound))
    wait_until(lambda: old.finished and new.started)

    assert live_run_tasks(bridge) == 1
    assert pool.running_count == 1
    assert pool.session_for(new.node_id) is new  # type: ignore[comparison-overlap]

    run_on(bridge, pool.close(new.node_id))
    wait_until(lambda: new.cancelled)
    run_on(bridge, _set(new.unwound))


def test_closing_a_queued_session_takes_it_out_of_the_queue(bridge: Bridge) -> None:
    """
    A session closed while it waits for a slot must not start when one opens.

    It has left ``sessions`` by then, so a run started for it is one no ``close``,
    ``send`` or ``interrupt`` can find.
    """
    pool = SessionPool(bridge, cap=1)
    running = SlowUnwindSession("running")
    waiting = SlowUnwindSession("waiting")

    async def submit() -> None:
        pool.submit(running)  # type: ignore[arg-type]
        pool.submit(waiting)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: running.started)
    assert pool.queued_count == 1

    bridge.drain()
    run_on(bridge, pool.close(waiting.node_id))
    assert pool.queued_count == 0
    # It never ran, so the pool is the only thing that can end its announced row.
    ended = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert [(i.node_id, i.state) for i in ended] == [(waiting.node_id, AgentState.DONE)]

    run_on(bridge, pool.close(running.node_id))
    run_on(bridge, _set(running.unwound))
    wait_until(lambda: running.finished)

    assert live_run_tasks(bridge) == 0
    assert not waiting.started
    assert pool.running_count == 0


# -- what the replaced run says, and when ---------------------------------------
#
# The store attributes an intent by node id and cannot tell two runs of one id
# apart, so anything the earlier run emits after the later one is announced is
# read as the later one's. A declared end is final there: the relaunched session
# would read as finished for the rest of its life.


class ReportingSession(SlowUnwindSession):
    """
    A session that says what ``AgentSession`` says, when it says it.

    ``announce`` emits the spawn. A cancelled ``run`` reports its end only once it
    has unwound, which for the real one is after a client has disconnected and a
    subprocess has been reaped -- loop turns later than the cancel, not the same
    one. ``label`` rides in the spawn's task so two sessions of one id can be told
    apart on the queue.
    """

    def __init__(self, bridge: Bridge, name: str, label: str) -> None:
        super().__init__(name)
        self.bridge = bridge
        self.label = label

    def announce(self) -> None:
        super().announce()
        self.bridge.emit(AgentSpawned(self.node_id, None, self.label, "stub", time.monotonic()))

    async def run(self) -> None:
        try:
            await super().run()
        except asyncio.CancelledError:
            if self.teardown_requested:
                self.bridge.emit(AgentFinished(self.node_id, AgentState.DONE, time.monotonic()))
            raise


def lifecycle(bridge: Bridge) -> list[str]:
    """The spawns and ends on the queue, in the order the store will apply them."""
    said: list[str] = []
    for intent in bridge.drain():
        if isinstance(intent, AgentSpawned):
            said.append(f"spawned {intent.task}")
        elif isinstance(intent, AgentFinished):
            said.append(f"finished {intent.state.name}")
    return said


def test_a_session_resumed_after_a_close_is_announced_after_the_closed_run_ends(
    bridge: Bridge,
) -> None:
    """
    Close, then resume the same id before the closed run has finished unwinding.

    The closed run's DONE is still to come. The resumed session is neither
    announced nor started until it has been said, so the end on the queue is
    followed by the spawn and not the other way about.
    """
    pool = SessionPool(bridge, cap=2)
    old = ReportingSession(bridge, "resumed", "old")
    new = ReportingSession(bridge, "resumed", "new")

    async def submit_old() -> None:
        pool.submit(old)  # type: ignore[arg-type]

    run_on(bridge, submit_old())
    wait_until(lambda: old.started)

    async def close_then_relaunch() -> None:
        await pool.close(old.node_id)
        pool.submit(new)  # type: ignore[arg-type]

    run_on(bridge, close_then_relaunch())
    wait_until(lambda: old.cancelled)
    # Several round trips through the loop: the run is parked, not merely slow.
    for _ in range(3):
        assert live_run_tasks(bridge) == 1
    assert not new.announced
    assert not new.started
    assert pool.session_for(new.node_id) is new  # type: ignore[comparison-overlap]

    run_on(bridge, _set(old.unwound))
    wait_until(lambda: new.started)
    assert lifecycle(bridge) == ["spawned old", "finished DONE", "spawned new"]
    assert pool.running_count == 1

    run_on(bridge, _set(new.unwound))
    run_on(bridge, pool.shutdown())


def test_a_session_submitted_over_a_live_one_is_announced_after_the_live_one_ends(
    bridge: Bridge,
) -> None:
    """
    Resume an id whose session was never closed.

    ``submit`` ends the live one itself, so the cancel and the registration are one
    call and the earlier run's end is always still to come.
    """
    pool = SessionPool(bridge, cap=2)
    old = ReportingSession(bridge, "resumed", "old")
    new = ReportingSession(bridge, "resumed", "new")

    async def submit(session: ReportingSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(old))
    wait_until(lambda: old.started)
    run_on(bridge, submit(new))
    wait_until(lambda: old.cancelled)
    for _ in range(3):
        assert live_run_tasks(bridge) == 1
    assert not new.announced
    assert not new.started

    run_on(bridge, _set(old.unwound))
    wait_until(lambda: new.started)
    assert lifecycle(bridge) == ["spawned old", "finished DONE", "spawned new"]

    run_on(bridge, _set(new.unwound))
    run_on(bridge, pool.shutdown())


def test_a_successor_starts_when_the_run_before_it_was_cancelled_before_its_first_step(
    bridge: Bridge,
) -> None:
    """
    Two submits under one id with no loop turn between them.

    The first run's task is cancelled before it has executed anything, so the
    coroutine is never entered and no ``finally`` inside it runs. The task still
    ends, and that is what releases the session held behind it. The session never
    ran to say its own end, so the pool says it, ahead of the successor's spawn.
    """
    pool = SessionPool(bridge, cap=2)
    old = ReportingSession(bridge, "resumed", "old")
    new = ReportingSession(bridge, "resumed", "new")

    async def submit_both() -> None:
        pool.submit(old)  # type: ignore[arg-type]
        pool.submit(new)  # type: ignore[arg-type]

    run_on(bridge, submit_both())
    wait_until(lambda: new.started)
    assert not old.started
    assert lifecycle(bridge) == ["spawned old", "finished DONE", "spawned new"]
    assert live_run_tasks(bridge) == 1
    assert pool.running_count == 1

    run_on(bridge, _set(new.unwound))
    run_on(bridge, pool.shutdown())


def test_closing_a_session_held_behind_an_ending_run_means_it_never_starts(
    bridge: Bridge,
) -> None:
    """
    The held session has no row of its own, so closing it says nothing: the run it
    waited on ends the row they share, once.
    """
    pool = SessionPool(bridge, cap=2)
    old = ReportingSession(bridge, "resumed", "old")
    dropped = ReportingSession(bridge, "resumed", "dropped")

    async def submit(session: ReportingSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(old))
    wait_until(lambda: old.started)
    run_on(bridge, submit(dropped))
    wait_until(lambda: old.cancelled)

    run_on(bridge, pool.close(dropped.node_id))
    assert pool.session_for(dropped.node_id) is None
    assert dropped.teardown_requested

    run_on(bridge, _set(old.unwound))
    wait_until(lambda: live_run_tasks(bridge) == 0)
    assert lifecycle(bridge) == ["spawned old", "finished DONE"]
    assert not (dropped.announced or dropped.started)
    assert pool.running_count == 0


def test_the_last_session_submitted_behind_an_ending_run_is_the_one_that_runs(
    bridge: Bridge,
) -> None:
    """
    One id holds one session, held or not: a later submit replaces an earlier one
    that has not been admitted, and the replaced one is never announced.
    """
    pool = SessionPool(bridge, cap=2)
    old = ReportingSession(bridge, "resumed", "old")
    replaced = ReportingSession(bridge, "resumed", "replaced")
    last = ReportingSession(bridge, "resumed", "last")

    async def submit(session: ReportingSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(old))
    wait_until(lambda: old.started)
    run_on(bridge, submit(replaced))
    wait_until(lambda: old.cancelled)
    run_on(bridge, submit(last))
    assert pool.session_for(last.node_id) is last  # type: ignore[comparison-overlap]

    run_on(bridge, _set(old.unwound))
    wait_until(lambda: last.started)
    assert lifecycle(bridge) == ["spawned old", "finished DONE", "spawned last"]
    assert not (replaced.announced or replaced.started)
    assert replaced.teardown_requested
    assert live_run_tasks(bridge) == 1

    run_on(bridge, _set(last.unwound))
    run_on(bridge, pool.shutdown())


def test_a_held_session_keeps_the_slot_the_run_before_it_gave_up(bridge: Bridge) -> None:
    """
    A live session relaunched at a full pool is still a running one afterwards.

    Sessions do not end by themselves. Were the freed slot to go to whatever was
    submitted next, the relaunch would queue behind work that may never finish.
    """
    pool = SessionPool(bridge, cap=1)
    old = ReportingSession(bridge, "resumed", "old")
    new = ReportingSession(bridge, "resumed", "new")
    other = ReportingSession(bridge, "other", "other")

    async def submit(session: ReportingSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(old))
    wait_until(lambda: old.started)
    run_on(bridge, submit(new))
    run_on(bridge, submit(other))
    wait_until(lambda: old.cancelled)
    assert other.announced
    assert not other.started
    assert pool.queued_count == 1

    # ``set_cap`` drains: the held slot is not the queue's to take there either.
    async def same_cap() -> None:
        pool.set_cap(1)

    run_on(bridge, same_cap())
    assert not other.started

    run_on(bridge, _set(old.unwound))
    wait_until(lambda: new.started)
    assert not other.started
    assert (pool.running_count, pool.queued_count) == (1, 1)

    run_on(bridge, _set(new.unwound))
    run_on(bridge, pool.shutdown())


def test_shutdown_waits_for_a_closed_run_that_has_not_ended(bridge: Bridge) -> None:
    """
    A closed session's subprocess is live until its run has unwound, and the pool
    stops counting it at the close. Shutdown answers for it all the same, and
    nothing held behind it is started on the way out.
    """
    pool = SessionPool(bridge, cap=2)
    old = ReportingSession(bridge, "resumed", "old")
    new = ReportingSession(bridge, "resumed", "new")

    async def submit_old() -> None:
        pool.submit(old)  # type: ignore[arg-type]

    run_on(bridge, submit_old())
    wait_until(lambda: old.started)

    async def close_then_relaunch() -> None:
        await pool.close(old.node_id)
        pool.submit(new)  # type: ignore[arg-type]

    run_on(bridge, close_then_relaunch())
    wait_until(lambda: old.cancelled)
    assert pool.running_count == 0

    stopping = bridge.submit(pool.shutdown())
    for _ in range(3):
        assert live_run_tasks(bridge) == 1
    assert not stopping.done()

    run_on(bridge, _set(old.unwound))
    stopping.result(timeout=TIMEOUT)
    assert old.finished
    assert live_run_tasks(bridge) == 0
    assert not (new.announced or new.started)
    assert lifecycle(bridge) == ["spawned old", "finished DONE"]


# -- a run that will not end, and a run that never began -------------------------


class AbsorbsItsCancel(ReportingSession):
    """
    A session whose ``run`` takes cancellations and carries on.

    What a ``suppress(BaseException)`` round an await does to a cancel delivered
    there. It absorbs ``absorbs`` of them; the next one ends it, as does setting
    ``unwound``.
    """

    def __init__(self, bridge: Bridge, name: str, label: str, absorbs: int = 1) -> None:
        super().__init__(bridge, name, label)
        self.absorbs = absorbs
        self.cancels = 0

    async def run(self) -> None:
        self.started = True
        assert self._hold is not None
        try:
            await self._hold.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            self.cancels = 1
        while not self.unwound.is_set():
            try:
                await self.unwound.wait()
            except asyncio.CancelledError:
                self.cancels += 1
                if self.cancels > self.absorbs:
                    raise
        self.finished = True


def test_a_run_that_absorbed_its_cancel_is_still_ended_by_shutdown(bridge: Bridge) -> None:
    """
    The pool cancels a closed run once and then has no way back to it. ``shutdown``
    is the exception: after its grace it names the run and cancels it again, so
    quitting does not wait on it for ever and does not leave it running.
    """
    pool = SessionPool(bridge, cap=2, shutdown_grace_s=0.2)
    stuck = AbsorbsItsCancel(bridge, "stuck", "stuck")

    async def submit() -> None:
        pool.submit(stuck)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: stuck.started)
    run_on(bridge, pool.close(stuck.node_id))
    wait_until(lambda: stuck.cancelled)
    assert live_run_tasks(bridge) == 1
    assert pool.session_for(stuck.node_id) is None
    run_on(bridge, pool.close(stuck.node_id))  # nothing left for a second close to find
    assert live_run_tasks(bridge) == 1

    mark = LOG.snapshot()[1]
    stopping = bridge.submit(pool.shutdown())
    try:
        assert stopping.result(timeout=1.0) == ()
        assert live_run_tasks(bridge) == 0
        assert stuck.cancels == 2
        said = pool_log_since(mark)
        assert [level for level, _ in said] == [Level.ERROR]
        assert "stuck had not ended 0.2s after it was cancelled" in said[0][1]
    finally:
        run_on(bridge, _set(stuck.unwound))


def test_shutdown_returns_and_names_a_run_that_no_cancel_ends(bridge: Bridge) -> None:
    """
    ``shutdown`` returns whatever a run does. One that outlasts the second cancel
    too is returned by node and named in the log, beside one that ended properly
    and is not.
    """
    pool = SessionPool(bridge, cap=2, shutdown_grace_s=0.1, shutdown_reap_s=0.1)
    immovable = AbsorbsItsCancel(bridge, "immovable", "immovable", absorbs=99)
    fine = SlowUnwindSession("fine")

    async def submit() -> None:
        pool.submit(immovable)  # type: ignore[arg-type]
        pool.submit(fine)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: immovable.started and fine.started)
    run_on(bridge, _set(fine.unwound))
    assert pool.shutdown_bound_s == pytest.approx(0.2)

    mark = LOG.snapshot()[1]
    began = time.monotonic()
    try:
        left = bridge.submit(pool.shutdown()).result(timeout=2.0)
        assert time.monotonic() - began >= 0.2
        assert left == (immovable.node_id,)
        assert immovable.cancels == 2
        assert immovable.teardown_requested
        errors = [text for level, text in pool_log_since(mark) if level is Level.ERROR]
        assert len(errors) == 2
        assert errors[0].startswith("immovable had not ended")
        assert errors[1].startswith("immovable did not end when cancelled a second time")
        assert live_run_tasks(bridge) == 1
        assert pool.running_count == 0
    finally:
        run_on(bridge, _set(immovable.unwound))
        wait_until(lambda: immovable.finished)


def test_a_run_that_raised_while_ending_is_not_left_for_asyncio_to_report(
    bridge: Bridge,
) -> None:
    """
    ``shutdown`` waits without raising what its runs raise, and reads it, so a run
    that failed on the way out is not reported by the loop as an exception nobody
    retrieved when its task is collected.
    """

    class FailsOnTheWayOut(SlowUnwindSession):
        async def run(self) -> None:
            try:
                await super().run()
            except asyncio.CancelledError:
                raise RuntimeError("the transport went first") from None

    pool = SessionPool(bridge, cap=2)
    failing = FailsOnTheWayOut("failing")
    reported: list[str] = []

    async def submit() -> None:
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: reported.append(str(context.get("message")))
        )
        pool.submit(failing)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: failing.started)
    run_on(bridge, _set(failing.unwound))
    assert run_on(bridge, pool.shutdown()) == ()

    async def collect() -> None:
        gc.collect()

    run_on(bridge, collect())
    run_on(bridge, collect())
    assert reported == []


def test_a_session_closed_before_its_run_has_taken_a_step_has_its_row_ended(
    bridge: Bridge,
) -> None:
    """
    A task cancelled before its first step never enters its coroutine, so the
    session never runs and cannot say its own end. Exactly one end: the list would
    show a second.
    """
    pool = SessionPool(bridge, cap=1)
    quick = ReportingSession(bridge, "quick", "quick")

    async def submit_then_close() -> None:
        pool.submit(quick)  # type: ignore[arg-type]
        await pool.close(quick.node_id)  # no suspension inside close: same loop turn

    run_on(bridge, submit_then_close())
    wait_until(lambda: live_run_tasks(bridge) == 0)
    assert not quick.started
    assert lifecycle(bridge) == ["spawned quick", "finished DONE"]


def test_closing_a_session_the_drain_has_just_started_ends_its_row(bridge: Bridge) -> None:
    """
    The same state reached without a submit: a close drains the queue into a start,
    and the next close on the loop finds that run before it has taken a step.
    """
    pool = SessionPool(bridge, cap=1)
    running = ReportingSession(bridge, "running", "running")
    waiting = ReportingSession(bridge, "waiting", "waiting")

    async def submit() -> None:
        pool.submit(running)  # type: ignore[arg-type]
        pool.submit(waiting)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: running.started)

    async def close_both() -> None:
        await pool.close(running.node_id)  # drains: `waiting` is started, not yet stepped
        await pool.close(waiting.node_id)

    run_on(bridge, close_both())
    run_on(bridge, _set(running.unwound))
    wait_until(lambda: live_run_tasks(bridge) == 0)
    ended = [i.node_id for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert not waiting.started
    assert sorted(ended) == sorted([running.node_id, waiting.node_id])


def test_shutdown_says_no_end_for_a_session_that_never_ran(bridge: Bridge) -> None:
    """
    ``shutdown`` in the loop turn of the submit cancels one run before its first
    step and drops one session from the queue. Both were announced and neither is
    ended: the lifecycle is the two spawns alone, where ``close`` would have said
    an end for each. Nothing reads the difference while ``shutdown`` is only called
    after the frame loop has returned.
    """
    pool = SessionPool(bridge, cap=1)
    unstepped = ReportingSession(bridge, "unstepped", "unstepped")
    queued = ReportingSession(bridge, "queued", "queued")

    async def submit_then_shut_down() -> tuple[tuple[str, str | None], ...]:
        pool.submit(unstepped)  # type: ignore[arg-type]
        pool.submit(queued)  # type: ignore[arg-type]
        return await pool.shutdown()  # no suspension before its cancels: same loop turn

    assert run_on(bridge, submit_then_shut_down()) == ()
    assert live_run_tasks(bridge) == 0
    assert not (unstepped.started or queued.started)
    assert lifecycle(bridge) == ["spawned unstepped", "spawned queued"]


class AnnouncesFromRun(ReportingSession):
    """
    A session whose ``run`` opens by announcing, as ``AgentSession.run`` does, on
    top of the announce the pool has already made.
    """

    async def run(self) -> None:
        self.announce()
        await super().run()


def test_a_session_that_announces_from_its_run_is_spawned_twice_under_one_id(
    bridge: Bridge,
) -> None:
    """
    The pool announces at ``submit`` and the run announces again on its first
    step, so the store is handed two spawns for one node with whatever was applied
    between them. A queued session is where that interval is long: its second
    spawn arrives when a slot frees, not on the next loop turn.
    """
    pool = SessionPool(bridge, cap=1)
    first = AnnouncesFromRun(bridge, "first", "first")
    second = AnnouncesFromRun(bridge, "second", "second")

    async def submit() -> None:
        pool.submit(first)  # type: ignore[arg-type]
        pool.submit(second)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: first.started)
    assert lifecycle(bridge) == ["spawned first", "spawned second", "spawned first"]

    # The slot is free at the cancel, so the queued session's run -- and with it the
    # second spawn -- comes ahead of the closed run's end.
    run_on(bridge, pool.close(first.node_id))
    wait_until(lambda: second.started)
    run_on(bridge, _set(first.unwound))
    wait_until(lambda: live_run_tasks(bridge) == 1)
    assert lifecycle(bridge) == ["spawned second", "finished DONE"]

    run_on(bridge, _set(second.unwound))
    assert run_on(bridge, pool.shutdown()) == ()


# -- a submit that arrives after shutdown has begun --------------------------------


def test_a_session_submitted_while_shutdown_waits_is_not_left_running(bridge: Bridge) -> None:
    """
    ``shutdown`` answers for the runs there were when it began. A session started
    during its wait is one it returns without, so ``submit`` refuses and says so.
    """
    pool = SessionPool(bridge, cap=2)
    old = SlowUnwindSession("old")
    late = SlowUnwindSession("late")

    async def submit(session: SlowUnwindSession) -> bool:
        return pool.submit(session)  # type: ignore[arg-type]

    assert run_on(bridge, submit(old)) is True
    wait_until(lambda: old.started)

    stopping = bridge.submit(pool.shutdown())
    wait_until(lambda: old.cancelled)
    assert run_on(bridge, submit(late)) is False  # a launch that was already in flight
    for _ in range(3):
        live_run_tasks(bridge)  # round trips, so a start has had its chance

    run_on(bridge, _set(old.unwound))
    stopping.result(timeout=TIMEOUT)
    try:
        assert live_run_tasks(bridge) == 0
        assert not (late.announced or late.started)
        assert pool.session_for(late.node_id) is None
    finally:
        assert late._hold is not None
        run_on(bridge, _set(late._hold))
        run_on(bridge, _set(late.unwound))


def test_a_session_submitted_under_an_unwinding_id_while_shutdown_waits_is_not_started(
    bridge: Bridge,
) -> None:
    """
    The held route: the id has a run still ending, so the session would be kept for
    it and admitted when that run ends, which is during the wait.
    """
    pool = SessionPool(bridge, cap=2)
    old = SlowUnwindSession("old")
    late = SlowUnwindSession("old")

    async def submit(session: SlowUnwindSession) -> bool:
        return pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(old))
    wait_until(lambda: old.started)

    run_on(bridge, pool.close(old.node_id))
    stopping = bridge.submit(pool.shutdown())
    wait_until(lambda: old.cancelled)
    assert run_on(bridge, submit(late)) is False
    for _ in range(3):
        live_run_tasks(bridge)

    run_on(bridge, _set(old.unwound))
    stopping.result(timeout=TIMEOUT)
    try:
        assert not (late.announced or late.started)
        assert live_run_tasks(bridge) == 0
        assert pool.session_for(late.node_id) is None
    finally:
        assert late._hold is not None
        run_on(bridge, _set(late._hold))
        run_on(bridge, _set(late.unwound))


# -- the held interval -------------------------------------------------------------


class CannotAnnounce(ReportingSession):
    def announce(self) -> None:
        raise RuntimeError("no row")


def test_a_held_successor_whose_announce_raises_is_not_left_registered(bridge: Bridge) -> None:
    """
    The admit runs in a task's done callback, where a raise reaches nobody. The
    session is not left findable with no row, the log says which it was, and the
    slot that was kept for it goes to the queue.
    """
    pool = SessionPool(bridge, cap=1)
    old = ReportingSession(bridge, "resumed", "old")
    new = CannotAnnounce(bridge, "resumed", "new")
    queued = ReportingSession(bridge, "queued", "queued")

    async def submit(session: ReportingSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(old))
    wait_until(lambda: old.started)
    run_on(bridge, submit(new))  # held: returns normally
    run_on(bridge, submit(queued))
    wait_until(lambda: old.cancelled)
    assert not queued.started

    mark = LOG.snapshot()[1]
    run_on(bridge, _set(old.unwound))
    wait_until(lambda: queued.started)
    assert not new.started
    assert pool.session_for(new.node_id) is None
    errors = [text for level, text in pool_log_since(mark) if level is Level.ERROR]
    assert len(errors) == 1
    assert errors[0].startswith("resumed could not be started") and "no row" in errors[0]

    run_on(bridge, _set(queued.unwound))
    run_on(bridge, pool.shutdown())


def test_a_submitted_session_whose_announce_raises_is_not_left_registered(bridge: Bridge) -> None:
    """
    The direct route, where the raise does reach the caller.
    """
    pool = SessionPool(bridge, cap=2)
    new = CannotAnnounce(bridge, "fresh", "new")

    async def submit() -> None:
        pool.submit(new)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="no row"):
        run_on(bridge, submit())
    assert pool.session_for(new.node_id) is None
    assert pool.running_count == 0


def test_the_log_says_what_a_relaunch_replaced(bridge: Bridge) -> None:
    """
    A session stays registered after its run ends by itself, so relaunching a
    finished or failed one finds it. That is the ordinary resume, and it is not the
    warning that a live session was ended.
    """
    pool = SessionPool(bridge, cap=1)
    ended = StubSession("resumed")
    again = SlowUnwindSession("resumed")
    over_live = SlowUnwindSession("resumed")
    queued = SlowUnwindSession("queued")
    over_queued = SlowUnwindSession("queued")
    over_held = SlowUnwindSession("resumed")

    async def submit(session: StubSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    def said_after(session: StubSession) -> list[tuple[Level, str]]:
        mark = LOG.snapshot()[1]
        run_on(bridge, submit(session))
        return pool_log_since(mark)

    assert said_after(ended) == []
    wait_until(lambda: ended.finished and pool.running_count == 0)
    assert pool.session_for(ended.node_id) is ended  # type: ignore[comparison-overlap]

    assert said_after(again) == [(Level.INFO, "resumed had ended; its relaunch replaces it")]
    wait_until(lambda: again.started)

    assert said_after(queued)[0][1].startswith("queued queued")
    assert said_after(over_queued)[0] == (Level.INFO, "queued was queued; its relaunch replaces it")

    assert said_after(over_live) == [
        (Level.WARN, "resumed was still live; ended it for its relaunch"),
        (Level.INFO, "resumed waits for its previous run to end"),
    ]
    assert said_after(over_held)[0] == (
        Level.INFO,
        "resumed was waiting for its previous run to end; its relaunch replaces it",
    )

    # Set first: ``shutdown`` waits for the runs it cancels.
    for session in (again, over_held, over_queued):
        run_on(bridge, _set(session.unwound))
    assert run_on(bridge, pool.shutdown()) == ()


def test_closing_a_held_relaunch_says_that_it_was_discarded(bridge: Bridge) -> None:
    """
    The row is the ending run's, so a second close on it lands on the relaunch
    behind it. Nothing on screen shows that; the log is where it is said. An
    ordinary close says nothing.
    """
    pool = SessionPool(bridge, cap=2)
    old = ReportingSession(bridge, "resumed", "old")
    held = ReportingSession(bridge, "resumed", "held")

    async def submit(session: ReportingSession) -> bool:
        return pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(old))
    wait_until(lambda: old.started)
    mark = LOG.snapshot()[1]
    run_on(bridge, pool.close(old.node_id))
    assert pool_log_since(mark) == []

    # Held is not refused: the session is registered and will be started.
    assert run_on(bridge, submit(held)) is True
    mark = LOG.snapshot()[1]
    run_on(bridge, pool.close(held.node_id))
    said = pool_log_since(mark)
    assert [level for level, _ in said] == [Level.WARN]
    assert said[0][1].startswith("resumed was closed while its relaunch waited")

    run_on(bridge, _set(old.unwound))
    wait_until(lambda: live_run_tasks(bridge) == 0)


# -- a session that waited is asked about its directory when it starts, not before --


def _contained_autonomous(bridge: Bridge, cwd: Path, **kwargs):
    from pptmstr.approval import Policy
    from pptmstr.driver import AgentSession
    from pptmstr.sandbox import containment_settings

    return AgentSession(
        bridge,
        task="write unattended",
        cwd=str(cwd),
        policy=Policy.AUTONOMOUS,
        containment=containment_settings(),
        **kwargs,
    )


def _stand_in_checkout(tmp_path: Path, monkeypatch) -> tuple:
    """
    A directory standing in for pptmstr's own checkout, a project directory beside
    it, and the list of SDK clients constructed -- which is the spawn.
    """
    checkout = tmp_path / "pptmstr-src"
    checkout.mkdir()
    sub = tmp_path / "proj" / "sub"
    sub.mkdir(parents=True)
    spawned: list[dict] = []

    def client(**kwargs):
        spawned.append(kwargs)
        raise RuntimeError("a CLI would have been spawned here")

    monkeypatch.setattr("pptmstr.driver.OWN_CHECKOUT", str(checkout), raising=False)
    monkeypatch.setattr("pptmstr.driver.ClaudeSDKClient", client)
    return checkout, sub, spawned


def _redirect(directory: Path, target: Path) -> None:
    """
    What a running contained session whose cwd is the parent can do with Bash.
    """
    directory.rename(directory.with_name(directory.name + ".bak"))
    directory.symlink_to(target, target_is_directory=True)


def _end_of(bridge: Bridge, session) -> AgentFinished:
    seen: list[AgentFinished] = []

    def ended() -> bool:
        seen.extend(
            i
            for i in bridge.drain()
            if isinstance(i, AgentFinished) and i.node_id == session.node_id
        )
        return bool(seen)

    wait_until(ended)
    return seen[0]


def test_a_queued_session_whose_directory_moved_into_the_checkout_spawns_nothing(
    bridge: Bridge, tmp_path, monkeypatch
) -> None:
    """
    The pool can hold a session for as long as the sessions ahead of it live, and
    the launch was judged before it was queued. The directory is asked about again
    when the slot frees, of whatever is there by then.
    """
    checkout, sub, spawned = _stand_in_checkout(tmp_path, monkeypatch)
    pool = SessionPool(bridge, cap=1)
    hold = asyncio.Event()
    ahead = StubSession("ahead", hold)
    queued = _contained_autonomous(bridge, sub)

    async def submit() -> None:
        pool.submit(ahead)  # type: ignore[arg-type]
        pool.submit(queued)

    run_on(bridge, submit())
    wait_until(lambda: ahead.started)
    assert pool.queued_count == 1

    _redirect(sub, checkout)
    run_on(bridge, _set(hold))

    end = _end_of(bridge, queued)
    assert spawned == []
    assert end.state is AgentState.FAILED
    assert end.error is not None and "own checkout" in end.error


def test_a_queued_session_whose_directory_stayed_put_is_started(
    bridge: Bridge, tmp_path, monkeypatch
) -> None:
    """
    The other half: without it, a check that refused every queued session would
    pass the test above.
    """
    _checkout, sub, spawned = _stand_in_checkout(tmp_path, monkeypatch)
    pool = SessionPool(bridge, cap=1)
    hold = asyncio.Event()
    ahead = StubSession("ahead", hold)
    queued = _contained_autonomous(bridge, sub)

    async def submit() -> None:
        pool.submit(ahead)  # type: ignore[arg-type]
        pool.submit(queued)

    run_on(bridge, submit())
    wait_until(lambda: ahead.started)
    run_on(bridge, _set(hold))

    _end_of(bridge, queued)
    assert len(spawned) == 1


def test_a_session_held_behind_an_ending_run_is_asked_about_its_directory_when_it_starts(
    bridge: Bridge, tmp_path, monkeypatch
) -> None:
    """
    A resume over a live run waits for that run to unwind, which is a second wait
    inside the pool that sits after the launch was judged.
    """
    checkout, sub, spawned = _stand_in_checkout(tmp_path, monkeypatch)
    pool = SessionPool(bridge, cap=1)
    live_id = "11111111-2222-3333-4444-555555555555"
    live = SlowUnwindSession(live_id)
    successor = _contained_autonomous(bridge, sub, resume=live_id)

    run_on(bridge, _submit(pool, live))
    wait_until(lambda: live.started)
    run_on(bridge, _submit(pool, successor))
    wait_until(lambda: live.cancelled)
    assert spawned == []

    _redirect(sub, checkout)
    run_on(bridge, _set(live.unwound))

    end = _end_of(bridge, successor)
    assert spawned == []
    assert end.state is AgentState.FAILED
    assert end.error is not None and "own checkout" in end.error


async def _submit(pool: SessionPool, session) -> None:
    pool.submit(session)


# -- a run that ends takes its sub-agents with it, by whichever path it ends --


class _OpenClient:
    """
    Enough of ClaudeSDKClient for ``AgentSession.run`` to connect and then sit in
    its read with a turn open -- the state a lead is in while a builder works
    under a foreground Agent call. ``ending`` ends that read: with the stream
    closing when it is set bare, with the exception when it is set carrying one.
    """

    def __init__(self) -> None:
        self.reading = asyncio.Event()
        self.ending: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def __aenter__(self) -> _OpenClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def query(self, prompt: object, session_id: str = "default") -> None:
        return None

    async def get_context_usage(self) -> dict:
        raise RuntimeError("no CLI attached")

    async def receive_messages(self):
        self.reading.set()
        await self.ending
        return
        yield  # an async generator, as the SDK's is


def _close_the_session(pool: SessionPool, session, client: _OpenClient):
    return pool.close(session.node_id)


async def _cancel_unasked(pool: SessionPool, session, client: _OpenClient) -> None:
    pool._running[session.node_id].cancel()


async def _close_the_stream(pool: SessionPool, session, client: _OpenClient) -> None:
    client.ending.set_result(None)


async def _die_in_the_read(pool: SessionPool, session, client: _OpenClient) -> None:
    client.ending.set_exception(RuntimeError("the transport died"))


async def _nothing_first(pool: SessionPool, session, client: _OpenClient) -> None:
    return None


# How the run ends, what the builder is settled as, and which of the two then owes
# the operator a failure row. One end is one row: where the root fails the row is
# the root's and the builder ends with its session, and where the root does not --
# a stream that closes with no failure standing ends it DONE -- the builder's is
# the only record that anything went wrong, so it keeps it.
_RUN_ENDS = {
    "closed by the operator": (_close_the_session, AgentState.CANCELLED, None),
    "relaunched over while live": (_nothing_first, AgentState.CANCELLED, None),
    "cancelled by nobody": (_cancel_unasked, AgentState.CANCELLED, "root"),
    "the stream closed mid-turn": (_close_the_stream, AgentState.FAILED, "builder"),
    "the read raised": (_die_in_the_read, AgentState.CANCELLED, "root"),
}


@pytest.mark.parametrize("how", list(_RUN_ENDS))
def test_a_builder_still_working_when_its_run_ends_is_settled_and_its_claim_can_be_given_back(
    bridge: Bridge, monkeypatch, how: str
) -> None:
    """
    The holder of a claim is ordinarily an agent with work open, so "still working
    when the session was closed, relaunched or died" is the usual state of a
    holder at that moment -- and no SubagentStop is fired here, which is what a
    cancelled run's teardown is read to do.

    Every way a run ends: the builder's record is terminal no later than its
    root's, nothing of the session is left counted as active, and the lead of the
    next run of that session can give the claim back. And the end is at most one
    failure in the operator's queue, not one per agent of the session.
    """
    from pptmstr.driver import AgentSession
    from pptmstr.intents import TaskClaimRequested, TaskDeclared, TaskReleased
    from pptmstr.model import SessionFailed, Task, TaskState
    from pptmstr.store import Store

    end, settled_as, owes = _RUN_ENDS[how]
    clients: list[_OpenClient] = []

    def client(**_: object) -> _OpenClient:
        clients.append(_OpenClient())
        return clients[-1]

    monkeypatch.setattr("pptmstr.driver.ClaudeSDKClient", client)
    pool = SessionPool(bridge, cap=2)
    store = Store()
    first = AgentSession(bridge, task="lead the work")
    root, builder = first.node_id, (first.session_id, "agent-a")

    run_on(bridge, _submit(pool, first))
    wait_until(lambda: bool(clients))
    run_on(bridge, clients[0].reading.wait())
    hook = {"hook_event_name": "SubagentStart", "agent_id": "agent-a", "agent_type": "builder"}
    run_on(bridge, first._subagent_start(hook, None, None))  # type: ignore[arg-type]
    store.apply_all(bridge.drain())
    store.apply(TaskDeclared(Task(id="t1", title="T"), root))
    store.apply(TaskClaimRequested(builder, request_id="k", task_id="t1"))
    assert store.snapshot().tasks["t1"].claimed_by == builder
    assert not store.snapshot().nodes[builder].state.is_terminal

    run_on(bridge, end(pool, first, clients[0]))
    if how != "relaunched over while live":
        wait_until(lambda: live_run_tasks(bridge) == 0)
        ended = bridge.drain()
        finished = [i.node_id for i in ended if isinstance(i, AgentFinished)]
        assert finished == [builder, root], "the builder's end is said, and before its root's"
        store.apply_all(ended)
        snap = store.snapshot()
        assert snap.nodes[builder].state is settled_as
        failures = [o.node for o in snap.needs_you if isinstance(o, SessionFailed)]
        assert failures == [{"root": root, "builder": builder}[owes]] if owes else not failures
        assert not snap.any_active
        assert not snap.of_earlier_run(builder), "nothing has started the session again yet"

    second = AgentSession(bridge, task="carry on", resume=first.session_id)
    run_on(bridge, _submit(pool, second))
    wait_until(lambda: len(clients) == 2)
    run_on(bridge, clients[1].reading.wait())
    store.apply_all(bridge.drain())

    snap = store.snapshot()
    assert snap.nodes[builder].state is settled_as
    assert snap.of_earlier_run(builder)
    (released,) = store.apply(TaskReleased(root, "t1", request_id="k1"))
    assert getattr(released, "refusal", "absent") is None
    assert store.snapshot().tasks["t1"].state is TaskState.PENDING

    run_on(bridge, pool.shutdown())


def test_send_says_whether_the_text_was_handed_to_a_client(bridge: Bridge) -> None:
    """
    The pool's answer is the session's. False is a text that was dropped: no
    session under the node, or one with no client -- which is also what a
    relaunch held behind an ending run is, under a row that still reads live.
    """
    pool = SessionPool(bridge, cap=2)
    session = StubSession("sess-1", asyncio.Event())

    async def submit() -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit())
    assert run_on(bridge, pool.send(("sess-1", "agent-a"), "taken")) is True
    session.takes = False
    assert run_on(bridge, pool.send(("sess-1", None), "dropped")) is False
    assert run_on(bridge, pool.send(("nobody", None), "nobody there")) is False
    assert session.sent == ["taken", "dropped"]


def test_a_run_is_held_while_it_runs_ends_waits_or_queues_and_not_once_it_is_over(
    bridge: Bridge,
) -> None:
    """
    What the shell asks before it takes a session off the fleet or marks a failed
    one ended. ``session_for`` is not that question: it also answers with a
    session whose run ended by itself, which has no process.
    """
    pool = SessionPool(bridge, cap=1)
    running = SlowUnwindSession("sess-1")
    queued = StubSession("sess-2", asyncio.Event())
    ended = StubSession("sess-3")

    async def submit(session: StubSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    run_on(bridge, submit(running))
    run_on(bridge, submit(queued))
    wait_until(lambda: running.started)
    assert pool.has_run(("sess-1", None)) and pool.has_run(("sess-1", "agent-a"))
    assert pool.has_run(("sess-2", None)), "queued for a slot"
    assert not pool.has_run(("nobody", None))

    # Closed and still unwinding, with a relaunch held behind it.
    successor = StubSession("sess-1", asyncio.Event())
    run_on(bridge, pool.close(("sess-1", None)))
    assert pool.has_run(("sess-1", None)), "cancelled, and not yet ended"
    run_on(bridge, submit(successor))
    assert pool.has_run(("sess-1", None))
    run_on(bridge, pool.close(("sess-1", None)))
    assert pool.has_run(("sess-1", None)), "the held relaunch is gone; the old run is not"
    run_on(bridge, _set(running.unwound))
    wait_until(lambda: not pool.has_run(("sess-1", None)))

    # A run that ended by itself leaves its session findable and no run.
    run_on(bridge, pool.close(("sess-2", None)))
    run_on(bridge, submit(ended))
    wait_until(lambda: ended.finished and not pool.has_run(("sess-3", None)))
    assert pool.session_for(("sess-3", None)) is ended


class _AskedMidMove:
    """
    Asks ``has_run`` from another thread at the one moment a move has taken a
    session out of a table and not yet put it in the next, and holds the move
    there until the answer is in or a short wait has passed.

    Without a lock the answer arrives at once, and it is "no run". With the move
    and the read under one lock the asker blocks, the wait here runs out, the
    move completes, and the answer is the pool after it.
    """

    def __init__(self, pool: SessionPool, node: tuple[str, None]) -> None:
        self.pool = pool
        self.node = node
        self.answers: list[bool] = []
        self._askers: list[threading.Thread] = []

    def ask(self) -> None:
        asker = threading.Thread(target=lambda: self.answers.append(self.pool.has_run(self.node)))
        self._askers.append(asker)
        asker.start()
        asker.join(0.2)

    def settle(self) -> list[bool]:
        for asker in self._askers:
            asker.join(TIMEOUT)
        return self.answers


def test_a_run_being_retired_is_never_seen_as_no_run_from_another_thread(
    bridge: Bridge,
) -> None:
    """
    ``_retire`` takes the task out of ``_running`` and enters it in
    ``_unwinding`` several statements later. "No run" read between the two is
    what lets the shell take a session off the fleet with its process still
    being ended.
    """
    pool = SessionPool(bridge, cap=1)
    running = SlowUnwindSession("sess-1")
    node = ("sess-1", None)
    asked = _AskedMidMove(pool, node)

    class Running(dict):  # type: ignore[type-arg]
        def pop(self, key, *default):  # type: ignore[no-untyped-def,override]
            had = key in self
            value = super().pop(key, *default)
            if had and key == node:
                asked.ask()
            return value

    async def submit() -> None:
        pool._running = Running()
        pool.submit(running)  # type: ignore[arg-type]

    run_on(bridge, submit())
    wait_until(lambda: running.started)
    run_on(bridge, pool.close(node))

    assert asked.settle() == [True], "asked once, mid-retire, and told there is a run"
    run_on(bridge, _set(running.unwound))
    wait_until(lambda: not pool.has_run(node))


def test_a_session_leaving_the_queue_or_the_hold_is_never_seen_as_no_run(
    bridge: Bridge,
) -> None:
    """
    The other two moves with a gap in them: a queued session is popped before
    its task is created, and a relaunch held behind an ending run is popped
    before it is admitted.
    """
    pool = SessionPool(bridge, cap=1)
    first = StubSession("sess-1", asyncio.Event())
    queued = StubSession("sess-2", asyncio.Event())
    from_queue = _AskedMidMove(pool, ("sess-2", None))

    class Waiting(deque):  # type: ignore[type-arg]
        def popleft(self):  # type: ignore[no-untyped-def]
            session = super().popleft()
            if session is queued:
                from_queue.ask()
            return session

    async def submit(session: StubSession) -> None:
        pool.submit(session)  # type: ignore[arg-type]

    async def watch_the_queue() -> None:
        pool._waiting = Waiting(pool._waiting)

    run_on(bridge, submit(first))
    run_on(bridge, submit(queued))
    run_on(bridge, watch_the_queue())
    assert first._hold is not None
    run_on(bridge, _set(first._hold))
    wait_until(lambda: queued.started)
    assert from_queue.settle() == [True]

    # A relaunch of the session now running, held until that run has unwound.
    slow = SlowUnwindSession("sess-3")
    successor = StubSession("sess-3", asyncio.Event())
    from_hold = _AskedMidMove(pool, ("sess-3", None))

    class Succeeding(dict):  # type: ignore[type-arg]
        def pop(self, key, *default):  # type: ignore[no-untyped-def,override]
            had = key in self
            value = super().pop(key, *default)
            if had:
                from_hold.ask()
            return value

    async def watch_the_hold() -> None:
        pool._succeeding = Succeeding()

    run_on(bridge, pool.close(("sess-2", None)))
    run_on(bridge, watch_the_hold())
    run_on(bridge, submit(slow))
    wait_until(lambda: slow.started)
    run_on(bridge, submit(successor))
    assert not successor.announced, "held behind the run that is ending"
    run_on(bridge, _set(slow.unwound))
    wait_until(lambda: successor.started)
    assert from_hold.settle() == [True]
