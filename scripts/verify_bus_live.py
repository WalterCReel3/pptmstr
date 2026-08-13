#!/usr/bin/env python3
"""
Does the bus actually work, end to end, against the real CLI?

The unit tests pin every piece separately: the reducer, the effect channel, the
Bridge's third crossing, the gate's stamp. None of them proves the assembled thing
runs -- the tools have to be advertised by the CLI, the stamp has to survive the
round trip into an in-process MCP handler, a handler has to park on a future that
a *different thread* completes from a snapshot, and the agent has to get its answer
back before the CLI's hook timeout.

This drives a real session with a stand-in operator: the loop below is the frame
loop from ``app.py`` with the UI removed and every approval answered immediately.

  1. Are the bus tools advertised to the model?
  2. Does declare_task/claim_task round-trip -- does the agent learn what it won?
  3. Does post_concern park as an ordinary approval, and is the sender the gate's
     rather than the model's?
  4. Does read_inbox come back with the concern, through the effect channel?

Usage:  .venv/bin/python scripts/verify_bus_live.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pptmstr import templates  # noqa: E402
from pptmstr.bridge import Bridge, Decision  # noqa: E402
from pptmstr.driver import AgentSession  # noqa: E402
from pptmstr.model import AgentState, ApprovalNeeded, ConcernState, TaskState  # noqa: E402
from pptmstr.store import Store  # noqa: E402

SOLO_TASK = (
    "You are coordinating work and must use your pptmstr tools. Do all of this, "
    "in order, and do not use any other tool:\n"
    "1. Call declare_task with task_id 'build', title 'build the thing'.\n"
    "2. Call declare_task with task_id 'test', title 'test the thing', "
    "depends_on ['build'].\n"
    "3. Call claim_task with no task_id. Report which task you were given.\n"
    "4. Call post_concern with to 'lead', subject 'ordering', body "
    "'test cannot start until build lands'.\n"
    "5. Call read_inbox and report what it returned.\n"
    "Then reply with a one-line summary."
)

# The team run. Deliberately says nothing about which tools to use or in what
# order: the briefing is supposed to carry that, and a prompt that spelled it out
# would be testing the prompt in this file rather than the one that ships.
TEAM_TASK = (
    "In scripts/, work out whether verify_bus_live.py would still report a correct "
    "result if the frame loop settled effects before applying intents rather than "
    "after. Use your team. Do not change any file."
)

approved: list[str] = []
# Which requests were actually answered by the frame loop, as opposed to abandoned
# at teardown with their fallback. The distinction is invisible in store state --
# InboxRead marks a concern DELIVERED and TaskClaimRequested marks a task CLAIMED at
# *apply* time, unconditionally -- so a verdict read off the board alone would report
# a round trip that never happened. Caught by the research team reviewing this file.
settled: list[str] = []


def main() -> int:
    team = "--team" in sys.argv
    shape = templates.RESEARCH if team else templates.SOLO
    task = TEAM_TASK if team else SOLO_TASK
    bridge = Bridge()
    bridge.start()
    store = Store()
    session = AgentSession(bridge, task=task, template=shape)
    session.announce()
    running = bridge.submit(session.run())

    deadline = time.monotonic() + (900 if team else 300)
    try:
        while not running.done() and time.monotonic() < deadline:
            # A session does not end when its turn does -- it goes to AWAITING_INPUT
            # and stays live for the next thing the operator says. Waiting on run()
            # would therefore always time out, which would read as a failure of the
            # bus rather than of this loop's exit condition.
            root = store.snapshot().get(session.node_id)
            if root is not None and root.state is AgentState.AWAITING_INPUT:
                break
            # The frame loop, minus the frame. Order is load-bearing and is the
            # order app.py uses: drain, apply, settle what was applied.
            effects = store.apply_all(bridge.drain())
            for effect in effects:
                if bridge.settle(effect):
                    settled.append(effect.request_id)

            # The stand-in operator. A real one takes as long as it likes (I8);
            # this one exists only to keep the run moving.
            for obligation in store.snapshot().needs_you:
                if isinstance(obligation, ApprovalNeeded):
                    approved.append(obligation.approval.summary)
                    bridge.resolve(obligation.approval.id, Decision(approved=True))
            time.sleep(0.01)
        if running.done():
            running.result(timeout=30)
    except Exception as exc:  # noqa: BLE001 - a probe reports rather than raises
        print(f"run failed: {type(exc).__name__}: {exc}")
    finally:
        # Drain once more so anything emitted during teardown is accounted for.
        for effect in store.apply_all(bridge.drain()):
            if bridge.settle(effect):
                settled.append(effect.request_id)
        # Read before stop(): abandon_all_requests resolves anything still waiting
        # with its fallback, which is exactly the state this needs to detect.
        stranded = bridge.asking_count
        bridge.stop()

    report(store, session, stranded)
    return 0


def report(store: Store, session: AgentSession, stranded: int) -> None:
    snap = store.snapshot()

    print("\n=== approvals the operator was asked for ===")
    for summary in approved:
        print(f"  {summary}")
    if not approved:
        print("  (none)")

    print(f"\n=== team: {session.template.name} ===")
    for role in session.template.role_names():
        node = session.resolve_role(role)
        print(f"  {role:<14} {'started ' + str(node[1]) if node else 'never started'}")

    print("\n=== task board ===")
    for task in snap.tasks.values():
        owner = session.role_of(task.claimed_by) if task.claimed_by else "-"
        deps = ",".join(task.depends_on) or "-"
        print(f"  {task.id:<8} {task.state.value:<10} owner={owner:<6} depends_on={deps}")
    if not snap.tasks:
        print("  (empty)")

    print("\n=== concerns ===")
    for concern in snap.concerns.values():
        print(f"  {concern.id}  {concern.state.value}")
        print(f"    from      {concern.sender}  (role: {session.role_of(concern.sender)})")
        print(f"    to        {concern.recipient}")
        print(f"    subject   {concern.subject}")
        print(f"    body      {concern.body}")
    if not snap.concerns:
        print("  (none)")

    print("\n=== verdict ===")

    print(f"\n=== the crossing ===\n  {len(settled)} request(s) answered by the frame loop")
    if stranded:
        print(f"  {stranded} STRANDED -- never settled, released by the fallback at teardown")
    else:
        print("  0 stranded")

    claimed = [t for t in snap.tasks.values() if t.state is TaskState.CLAIMED]
    blocked = [t for t in snap.tasks.values() if t.depends_on and not t.is_claimable(snap.tasks)]
    print(f"Q1/Q2 tasks declared: {len(snap.tasks)}, claimed: {len(claimed)}")
    # Deliberately not phrased as "the agent was told what it won" off board state
    # alone: the board says CLAIMED whether or not the reply ever reached the agent.
    print(f"      requests answered: {len(settled)}, stranded: {stranded}")
    if claimed and not stranded:
        print(f"      claimed {claimed[0].id!r}, and every reply was settled by the")
        print("      frame loop rather than by the teardown fallback.")
    if blocked:
        print(f"      {blocked[0].id!r} correctly unclaimable: dependencies unmet.")

    posts = [s for s in approved if s.startswith("message ")]
    print(f"Q3 post_concern parked as an ordinary approval: {bool(posts)} {posts}")
    # The earlier form of this compared the sender to the *root* node, so every
    # concern from a sub-agent -- which is all of them in team mode -- reported
    # False while the stamp was in fact correct. What matters is that the sender is
    # a real node of this session that resolves to a role, never a model-written
    # string.
    for concern in snap.concerns.values():
        session_ok = concern.sender[0] == session.session_id
        role = session.role_of(concern.sender)
        print(f"   {concern.id}: stamped session={session_ok} resolves_to={role!r}")

    delivered = [c for c in snap.concerns.values() if c.state is ConcernState.DELIVERED]
    cross = [c for c in snap.concerns.values() if c.sender != c.recipient and c.recipient[1]]
    print(f"Q4 read_inbox delivered {len(delivered)} concern(s).")
    print(f"   worker-to-worker concerns: {len(cross)} -- the thing SendMessage could")
    print("   not do without us plumbing agent ids into a worker's prompt.")


if __name__ == "__main__":
    raise SystemExit(main())
