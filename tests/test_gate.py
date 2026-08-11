"""
The approval gate end to end, without an SDK subprocess.

Drives ``AgentSession._pre_tool_use`` directly on a real Bridge and a real Store,
which is the whole path that matters: classify, park, block, resolve, release. The
CLI's role in that chain was verified separately by scripts/verify_hook_timeout.py.
"""

from __future__ import annotations

import threading
import time

import pytest

from pptmstr.bridge import Bridge, Decision
from pptmstr.driver import AgentSession
from pptmstr.model import AgentState
from pptmstr.store import Store

TIMEOUT = 5.0


@pytest.fixture()
def bridge():
    b = Bridge()
    b.start()
    try:
        yield b
    finally:
        b.stop()


def hook_input(tool_name: str, **args: object) -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": dict(args),
        "tool_use_id": "tu-1",
        "session_id": "s",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/tmp",
    }


def decision_of(output: dict) -> str:
    return output["hookSpecificOutput"]["permissionDecision"]


def pump(store: Store, bridge: Bridge, until, timeout: float = TIMEOUT) -> None:
    """Stand in for the frame loop: drain and apply until a condition holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        store.apply_all(bridge.drain())
        if until():
            return
        time.sleep(0.01)
    store.apply_all(bridge.drain())
    raise AssertionError("condition not reached")


# -- the non-blocking paths ----------------------------------------------------


def test_reads_are_allowed_without_parking(bridge: Bridge) -> None:
    session = AgentSession(bridge, "task")
    out = bridge.submit(session._pre_tool_use(hook_input("Read", file_path="/x"), None, {})).result(
        timeout=TIMEOUT
    )
    assert decision_of(out) == "allow"
    assert bridge.parked_count == 0


def test_headless_denies_rather_than_hanging(bridge: Bridge) -> None:
    """
    With no operator attached, a tool needing approval must fail closed immediately.
    Leaving it to hit the six-hour timeout would look like a hang.
    """
    session = AgentSession(bridge, "task", interactive=False)
    out = bridge.submit(
        session._pre_tool_use(hook_input("Write", file_path="/x", content="y"), None, {})
    ).result(timeout=TIMEOUT)
    assert decision_of(out) == "deny"
    assert "no operator" in out["hookSpecificOutput"]["permissionDecisionReason"]


# -- parking and release -------------------------------------------------------


def test_write_parks_the_agent_and_reaches_the_store(bridge: Bridge) -> None:
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()

    task = bridge.submit(
        session._pre_tool_use(hook_input("Write", file_path="/x", content="hello"), None, {})
    )
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    snap = store.snapshot()
    assert len(snap.approvals) == 1
    pending = snap.approvals[0]
    assert pending.tool_name == "Write"
    assert pending.diff is not None
    assert snap.nodes[session.node_id].state is AgentState.AWAITING_APPROVAL
    # I8: the app can idle while an agent waits on a human.
    assert snap.any_active is False
    assert not task.done()

    assert bridge.resolve(pending.id, Decision(approved=True))
    out = task.result(timeout=TIMEOUT)
    assert decision_of(out) == "allow"

    pump(store, bridge, lambda: not store.snapshot().approvals)
    assert store.snapshot().nodes[session.node_id].pending == ()


def test_rejection_carries_the_reason_to_the_model(bridge: Bridge) -> None:
    """
    §5.3: a rejection that explains itself is worth far more than a bare denial --
    proven live in step 3, where the agent adapted rather than retrying blindly.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    task = bridge.submit(session._pre_tool_use(hook_input("Bash", command="rm -rf /"), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    pending = store.snapshot().approvals[0]
    bridge.resolve(pending.id, Decision(approved=False, reason="never do that"))
    out = task.result(timeout=TIMEOUT)

    assert decision_of(out) == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "never do that"


def test_edit_then_approve_substitutes_the_arguments(bridge: Bridge) -> None:
    """
    The §5.3 capability: fix a wrong path and run the corrected call, rather than
    rejecting and waiting for the agent to try again.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    task = bridge.submit(
        session._pre_tool_use(hook_input("Write", file_path="/wrong", content="x"), None, {})
    )
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    pending = store.snapshot().approvals[0]
    bridge.resolve(
        pending.id,
        Decision(approved=True, edited_args={"file_path": "/right", "content": "x"}),
    )
    out = task.result(timeout=TIMEOUT)

    assert decision_of(out) == "allow"
    assert out["hookSpecificOutput"]["updatedInput"]["file_path"] == "/right"


def test_plain_approval_sends_no_updated_input(bridge: Bridge) -> None:
    """Absent, not an echo of the original -- so the CLI runs what the agent asked."""
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    task = bridge.submit(session._pre_tool_use(hook_input("Bash", command="ls"), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))
    bridge.resolve(store.snapshot().approvals[0].id, Decision(approved=True))
    out = task.result(timeout=TIMEOUT)
    assert "updatedInput" not in out["hookSpecificOutput"]


def test_one_parked_agent_does_not_block_another(bridge: Bridge) -> None:
    """I8 at the gate level, not just the Bridge level."""
    store = Store()
    blocked = AgentSession(bridge, "blocked")
    other = AgentSession(bridge, "other")
    blocked.announce()

    slow = bridge.submit(
        blocked._pre_tool_use(hook_input("Write", file_path="/x", content="y"), None, {})
    )
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    fast = bridge.submit(other._pre_tool_use(hook_input("Read", file_path="/y"), None, {}))
    assert decision_of(fast.result(timeout=TIMEOUT)) == "allow"
    assert not slow.done()

    bridge.resolve(store.snapshot().approvals[0].id, Decision(approved=False))
    slow.result(timeout=TIMEOUT)


def test_cancellation_clears_the_pending_row(bridge: Bridge) -> None:
    """
    The CLI's per-hook timeout arrives as a cancellation of the gate coroutine
    (verified in scripts/verify_hook_timeout.py). Leaving the row in the store would
    show the operator an approval that can never be answered.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    task = bridge.submit(
        session._pre_tool_use(hook_input("Write", file_path="/x", content="y"), None, {})
    )
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    task.cancel()
    pump(store, bridge, lambda: not store.snapshot().approvals)
    assert store.snapshot().nodes[session.node_id].pending == ()


def test_shutdown_releases_a_parked_gate() -> None:
    """A gate awaiting at shutdown must not leave a future nobody completes."""
    b = Bridge()
    b.start()
    session = AgentSession(b, "task")
    outcome: list[str] = []
    done = threading.Event()

    async def gate() -> None:
        out = await session._pre_tool_use(hook_input("Bash", command="ls"), None, {})
        outcome.append(decision_of(out))
        done.set()

    b.submit(gate())
    deadline = time.monotonic() + TIMEOUT
    while b.parked_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert b.parked_count == 1

    b.stop()
    assert done.wait(TIMEOUT)
    assert outcome == ["deny"]


# -- an approval must never be lost --------------------------------------------


def test_approval_for_an_unannounced_node_is_recovered(bridge: Bridge) -> None:
    """
    A dropped ApprovalRequested is a permanent hang: the agent blocks on a future
    only the operator can complete, and nothing appears in the queue to explain
    why. A sub-agent whose SubagentStart did not fire is how this happens.

    Every other intent for an unknown node is a no-op -- deliberately. This one
    cannot be, so it recovers a placeholder row instead.
    """
    from pptmstr.intents import ApprovalRequested
    from pptmstr.model import PendingApproval

    store = Store()
    ghost = ("sess-x", "agent-never-announced")
    store.apply(
        ApprovalRequested(
            ghost,
            PendingApproval(
                id="p1",
                node=ghost,
                tool_name="Write",
                tool_use_id="tu",
                raw_args={"file_path": "/tmp/x"},
                summary="Write /tmp/x",
                requested_at=1.0,
            ),
        )
    )
    snap = store.snapshot()
    assert len(snap.approvals) == 1
    assert snap.approvals[0].id == "p1"
    assert snap.nodes[ghost].state is AgentState.AWAITING_APPROVAL


def test_a_recovered_approval_can_be_resolved_normally(bridge: Bridge) -> None:
    """Recovery is worthless if the placeholder cannot then be answered."""
    store = Store()
    session = AgentSession(bridge, "task")
    # Deliberately no announce(): the node is unknown to the store.
    task = bridge.submit(
        session._pre_tool_use(hook_input("Write", file_path="/x", content="y"), None, {})
    )
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    pending = store.snapshot().approvals[0]
    assert bridge.resolve(pending.id, Decision(approved=True))
    assert decision_of(task.result(timeout=TIMEOUT)) == "allow"


def test_parked_futures_and_visible_queue_agree(bridge: Bridge) -> None:
    """
    The invariant the watchdog checks. Bridge.parked_count is how many agents are
    blocked; approvals is how many the operator can answer. A gap means a
    permanent hang with no other symptom.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    bridge.submit(session._pre_tool_use(hook_input("Bash", command="ls"), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))
    assert bridge.parked_count == len(store.snapshot().approvals)
