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


# -- the operator's rewrite is recorded, not just applied -------------------------
#
# `Concern.edited` is documented as the reason a concern is a record at all, and its
# only reducer writer -- the `ConcernEdited` arm -- has no emitter anywhere in
# pptmstr/. The operator's rewrite reaches the bus handler as ordinary edited
# arguments, so a Concern built from them was indistinguishable from one the sender
# wrote. These drive the real gate and the real MCP server rather than the reducer.


def _post_concern_hook(**args: object) -> dict[str, object]:
    from pptmstr.bus import qualified

    return hook_input(qualified("post_concern"), **args)


def _run_handler(session, arguments: dict) -> None:
    """Feed the gate's approved arguments to the real post_concern handler."""
    import asyncio

    import mcp.types as mcp_types

    from pptmstr.bus import build_server

    server = build_server(session)["instance"]
    handler = server.request_handlers[mcp_types.CallToolRequest]
    result = asyncio.run(
        handler(
            mcp_types.CallToolRequest(
                method="tools/call",
                params=mcp_types.CallToolRequestParams(name="post_concern", arguments=arguments),
            )
        )
    )
    assert not result.root.isError, result.root.content


def _approved_args(bridge: Bridge, store: Store, session, decision: Decision) -> dict:
    task = bridge.submit(
        session._pre_tool_use(
            _post_concern_hook(to="lead", subject="retry loop", body="original"), None, {}
        )
    )
    pump(store, bridge, lambda: bool(store.snapshot().approvals))
    bridge.resolve(store.snapshot().approvals[0].id, decision)
    out = task.result(timeout=TIMEOUT)
    assert decision_of(out) == "allow"
    return dict(out["hookSpecificOutput"]["updatedInput"])


def test_a_concern_the_operator_rewrote_is_recorded_as_edited(bridge: Bridge) -> None:
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    pump(store, bridge, lambda: bool(store.snapshot().nodes))

    args = _approved_args(
        bridge,
        store,
        session,
        Decision(
            approved=True,
            edited_args={"to": "lead", "subject": "retry loop", "body": "narrowed by the operator"},
        ),
    )
    _run_handler(session, args)
    pump(store, bridge, lambda: bool(store.snapshot().concerns))

    (concern,) = store.snapshot().concerns.values()
    assert concern.body == "narrowed by the operator"
    assert concern.edited


def test_a_concern_approved_untouched_is_not_marked_edited(bridge: Bridge) -> None:
    """
    The hazard that makes this a task rather than a line: the sender stamp is
    itself a rewrite of the arguments, so a comparison made after stamping would
    mark every approved concern as edited.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    pump(store, bridge, lambda: bool(store.snapshot().nodes))

    args = _approved_args(bridge, store, session, Decision(approved=True))
    _run_handler(session, args)
    pump(store, bridge, lambda: bool(store.snapshot().concerns))

    (concern,) = store.snapshot().concerns.values()
    assert concern.body == "original"
    assert not concern.edited


def test_opening_the_editor_and_changing_nothing_is_not_an_edit(bridge: Bridge) -> None:
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    pump(store, bridge, lambda: bool(store.snapshot().nodes))

    args = _approved_args(
        bridge,
        store,
        session,
        Decision(
            approved=True,
            edited_args={"to": "lead", "subject": "retry loop", "body": "original"},
        ),
    )
    _run_handler(session, args)
    pump(store, bridge, lambda: bool(store.snapshot().concerns))

    (concern,) = store.snapshot().concerns.values()
    assert not concern.edited


def test_a_sender_supplied_edit_stamp_is_overwritten(bridge: Bridge) -> None:
    """
    Same reason FROM_KEY is gate-written: a sender that could set this could claim
    the operator had vetted a message the operator never saw.

    The input must *contain* the forged key. An earlier version of this test
    passed `{"to": "lead"}` and asserted the absence of a key nobody had supplied,
    which is true of the defective code as well -- the stamp is a copy of the
    model's own arguments, so the only thing worth asserting is that a value
    already in there gets overwritten.
    """
    from pptmstr.bus import EDITED_KEY, qualified
    from pptmstr.driver import AgentSession as S

    session = S(bridge, "task")
    forged = {"to": "lead", "subject": "x", "body": "y", EDITED_KEY: True}
    stamped = session._stamp_bus_call(qualified("post_concern"), forged, ("s1", None))

    assert stamped is not None
    assert stamped[EDITED_KEY] is False


def test_the_gate_records_a_real_edit_on_the_same_key(bridge: Bridge) -> None:
    """The other half: overwriting must not make the honest case unreportable."""
    from pptmstr.bus import EDITED_KEY, qualified
    from pptmstr.driver import AgentSession as S

    session = S(bridge, "task")
    stamped = session._stamp_bus_call(
        qualified("post_concern"), {"to": "lead"}, ("s1", None), edited=True
    )

    assert stamped is not None
    assert stamped[EDITED_KEY] is True


def test_a_forged_edit_stamp_does_not_reach_the_concern(bridge: Bridge) -> None:
    """
    The same forgery through the whole path rather than one method: a model puts
    the key in its own arguments, the operator approves without touching it, and
    the stored record must still say nobody rewrote it.
    """
    from pptmstr.bus import EDITED_KEY

    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    pump(store, bridge, lambda: bool(store.snapshot().nodes))

    task = bridge.submit(
        session._pre_tool_use(
            _post_concern_hook(
                to="lead", subject="retry loop", body="original", **{EDITED_KEY: True}
            ),
            None,
            {},
        )
    )
    pump(store, bridge, lambda: bool(store.snapshot().approvals))
    bridge.resolve(store.snapshot().approvals[0].id, Decision(approved=True))
    out = task.result(timeout=TIMEOUT)
    assert decision_of(out) == "allow"

    _run_handler(session, dict(out["hookSpecificOutput"]["updatedInput"]))
    pump(store, bridge, lambda: bool(store.snapshot().concerns))

    (concern,) = store.snapshot().concerns.values()
    assert not concern.edited


# -- the spawn join (§3) -------------------------------------------------------
#
# `Agent` is in _REVIEW, so a spawn always passes through the gate with a human in
# it. Nothing drove _pre_tool_use for an Agent call before these tests, which is why
# an entry could be left behind by a refusal without anything noticing.


def _agent_hook(
    tool_use_id: str = "tu-agent", subagent_type: str = "reviewer"
) -> dict[str, object]:
    call = hook_input("Agent", description="review the diff", subagent_type=subagent_type)
    call["tool_use_id"] = tool_use_id
    return call


def _start_hook(agent_id: str, agent_type: str) -> dict[str, object]:
    return {
        "hook_event_name": "SubagentStart",
        "agent_id": agent_id,
        "agent_type": agent_type,
        "session_id": "s",
        "cwd": "/tmp",
        "transcript_path": "/tmp/t.jsonl",
    }


def _approve_a_spawn(
    session: AgentSession, store: Store, bridge: Bridge, tool_use_id: str, role: str = "reviewer"
) -> dict:
    """One spawn all the way through the gate, approved."""
    task = bridge.submit(session._pre_tool_use(_agent_hook(tool_use_id, role), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))
    bridge.resolve(store.snapshot().approvals[0].id, Decision(approved=True))
    out = task.result(timeout=TIMEOUT)
    pump(store, bridge, lambda: not store.snapshot().approvals)
    return out


def test_an_approved_spawn_is_admitted_to_the_ledger(bridge: Bridge) -> None:
    """
    The positive control. Skipping the admission on the approved path too would look
    correct and would silently route every sub-agent's progress and usage to the
    root instead.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    task = bridge.submit(session._pre_tool_use(_agent_hook(), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    bridge.resolve(store.snapshot().approvals[0].id, Decision(approved=True))
    assert decision_of(task.result(timeout=TIMEOUT)) == "allow"
    assert session._pending_spawns == {"reviewer": ["tu-agent"]}


def test_a_rejected_spawn_admits_nothing(bridge: Bridge) -> None:
    """
    A dead tool_use_id left behind by a refusal is joined to whichever sub-agent
    starts next, and that sub-agent's real messages then miss the map. It also
    occupies a slot the cap counts, for the session's life.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    task = bridge.submit(session._pre_tool_use(_agent_hook(), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    bridge.resolve(store.snapshot().approvals[0].id, Decision(approved=False, reason="no"))
    assert decision_of(task.result(timeout=TIMEOUT)) == "deny"
    assert session._pending_spawns == {}


def test_a_cancelled_spawn_admits_nothing(bridge: Bridge) -> None:
    """The six-hour hook timeout arrives as a cancellation, and the tool never runs."""
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()
    task = bridge.submit(session._pre_tool_use(_agent_hook(), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))

    task.cancel()
    pump(store, bridge, lambda: not store.snapshot().approvals)
    assert session._pending_spawns == {}


def test_a_headless_spawn_admits_nothing(bridge: Bridge) -> None:
    """No operator means the call is denied, so no SubagentStart will ever follow."""
    session = AgentSession(bridge, "task", interactive=False)
    out = bridge.submit(session._pre_tool_use(_agent_hook(), None, {})).result(timeout=TIMEOUT)
    assert decision_of(out) == "deny"
    assert session._pending_spawns == {}


def test_a_refused_spawn_does_not_steal_the_next_ones_join(bridge: Bridge) -> None:
    """
    The composed case the note describes: refuse one Agent call, approve the next,
    and the join must name the call that actually ran.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()

    refused = bridge.submit(session._pre_tool_use(_agent_hook("tu-dead"), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))
    bridge.resolve(store.snapshot().approvals[0].id, Decision(approved=False))
    refused.result(timeout=TIMEOUT)

    _approve_a_spawn(session, store, bridge, "tu-live")

    assert session._pending_spawns == {"reviewer": ["tu-live"]}


def test_a_calls_own_tool_use_id_is_used_even_while_another_spawn_waits(bridge: Bridge) -> None:
    """
    Two spawns can sit in the gate at once, and the operator answers in whatever
    order suits. Recording the id on entry made the second overwrite the first, so
    whichever was approved first bound the wrong id and the other bound none.

    Both ids have to survive, in the order the operator released them: the ledger is
    consumed FIFO, so an approval that appended out of order would hand the first
    sub-agent to start the other call's id.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()

    first = bridge.submit(session._pre_tool_use(_agent_hook("tu-first"), None, {}))
    pump(store, bridge, lambda: len(store.snapshot().approvals) == 1)
    second = bridge.submit(session._pre_tool_use(_agent_hook("tu-second"), None, {}))
    pump(store, bridge, lambda: len(store.snapshot().approvals) == 2)

    by_id = {p.tool_use_id: p.id for p in store.snapshot().approvals}
    bridge.resolve(by_id["tu-second"], Decision(approved=True))
    second.result(timeout=TIMEOUT)
    assert session._pending_spawns == {"reviewer": ["tu-second"]}

    bridge.resolve(by_id["tu-first"], Decision(approved=True))
    first.result(timeout=TIMEOUT)
    assert session._pending_spawns == {"reviewer": ["tu-second", "tu-first"]}


def test_an_edited_spawn_is_filed_under_the_role_that_will_run(bridge: Bridge) -> None:
    """
    Edit-then-approve runs the operator's arguments, so SubagentStart reports the
    role they typed. Filing the call under the role the model asked for would leave
    the started sub-agent unjoined and the edited entry stranded.
    """
    store = Store()
    session = AgentSession(bridge, "task")
    session.announce()

    task = bridge.submit(session._pre_tool_use(_agent_hook("tu-edited", "reviewer"), None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))
    bridge.resolve(
        store.snapshot().approvals[0].id,
        Decision(
            approved=True, edited_args={"description": "build it", "subagent_type": "builder"}
        ),
    )
    assert decision_of(task.result(timeout=TIMEOUT)) == "allow"

    assert session._pending_spawns == {"builder": ["tu-edited"]}


# -- the cap (2026-08-14-a-role-runs-one-agent, phase 4) ------------------------


def test_a_burst_of_spawns_is_capped_before_any_of_them_starts(bridge: Bridge) -> None:
    """
    The burst the cap exists to bound. Every Agent hook can be answered before the
    first SubagentStart fires, so a count taken from the live set alone reads zero
    for all of them and admits the lot.
    """
    store = Store()
    session = AgentSession(bridge, "task", subagent_cap=2)
    session.announce()

    _approve_a_spawn(session, store, bridge, "tu-1")
    _approve_a_spawn(session, store, bridge, "tu-2")
    assert session._live_subagents == set()

    out = bridge.submit(session._pre_tool_use(_agent_hook("tu-3"), None, {})).result(
        timeout=TIMEOUT
    )
    assert decision_of(out) == "deny"
    # Refused at the hook, so it never reached the operator: parking a call the
    # session has already decided against would ask a human to approve nothing.
    assert bridge.parked_count == 0
    assert not store.snapshot().approvals


def test_the_cap_refusal_says_what_the_lead_can_do_instead(bridge: Bridge) -> None:
    """
    A capacity refusal is a timing problem, not a spelling one. A message that does
    not name the ceiling or the alternative gets the same call retried verbatim.
    """
    store = Store()
    session = AgentSession(bridge, "task", subagent_cap=1)
    session.announce()
    _approve_a_spawn(session, store, bridge, "tu-1")

    out = bridge.submit(session._pre_tool_use(_agent_hook("tu-2"), None, {})).result(
        timeout=TIMEOUT
    )
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "cap is 1" in reason
    assert "the call itself is fine" in reason
    assert "depends_on" in reason


def test_a_finished_subagent_frees_a_slot(bridge: Bridge) -> None:
    """
    The cap counts what is outstanding, not what the session has ever run. Counting
    _seen_subagents -- the set the resume signal reads, which never shrinks -- would
    make the ceiling a lifetime quota that wedges the session.
    """
    store = Store()
    session = AgentSession(bridge, "task", subagent_cap=1)
    session.announce()
    _approve_a_spawn(session, store, bridge, "tu-1")

    bridge.submit(session._subagent_start(_start_hook("a-1", "reviewer"), None, {})).result(
        timeout=TIMEOUT
    )
    assert session._live_subagents == {"a-1"}
    denied = bridge.submit(session._pre_tool_use(_agent_hook("tu-2"), None, {})).result(
        timeout=TIMEOUT
    )
    assert decision_of(denied) == "deny"

    bridge.submit(
        session._subagent_stop(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "a-1",
                "last_assistant_message": "done",
                "session_id": "s",
                "cwd": "/tmp",
                "transcript_path": "/tmp/t.jsonl",
            },
            None,
            {},
        )
    ).result(timeout=TIMEOUT)

    assert decision_of(_approve_a_spawn(session, store, bridge, "tu-3")) == "allow"


def test_the_cap_gates_spawns_and_nothing_else(bridge: Bridge) -> None:
    """
    Everything else has to keep working while the session is full -- a worker that
    cannot read a file because the lead is at its agent ceiling is a wedge, not a
    bound.
    """
    store = Store()
    session = AgentSession(bridge, "task", subagent_cap=0)
    session.announce()

    out = bridge.submit(session._pre_tool_use(hook_input("Read", file_path="/x"), None, {})).result(
        timeout=TIMEOUT
    )
    assert decision_of(out) == "allow"
    assert not store.snapshot().approvals


def test_a_spawn_from_inside_a_subagent_is_not_counted(bridge: Bridge) -> None:
    """
    The ceiling is per session and counts the sub-agents this session started. An
    Agent call carrying an agent_id is a sub-agent starting its own, which is not
    admitted to the ledger and is not counted here either -- it still parks, so the
    operator remains the bound on that branch.

    Pinned because it is a hole in the ceiling rather than a decision that reads
    obviously from the code: `spawn` is false for these calls, and both the ledger
    and the cap follow that one flag.
    """
    store = Store()
    session = AgentSession(bridge, "task", subagent_cap=0)
    session.announce()

    nested = _agent_hook("tu-nested")
    nested["agent_id"] = "a-1"
    task = bridge.submit(session._pre_tool_use(nested, None, {}))
    pump(store, bridge, lambda: bool(store.snapshot().approvals))
    bridge.resolve(store.snapshot().approvals[0].id, Decision(approved=True))

    assert decision_of(task.result(timeout=TIMEOUT)) == "allow"
    assert session._pending_spawns == {}
