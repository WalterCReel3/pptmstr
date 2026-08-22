#!/usr/bin/env python3
"""
Does pptmstr's own wait loop forward a turn the CLI is known to dispatch?

``scripts/verify_lead_turn_during_subagents.py`` settled the transport: the CLI
dispatches a user message that arrives while a background task is outstanding
(two runs, both ANSWERED, 2026-08-21). But that probe drives ``ClaudeSDKClient``
directly and never enters ``AgentSession``. The planning record it fed
(``planning/2026-08-21-a-lead-is-already-free-while-its-workers-run.md``) names
the remaining step: the same probe through ``AgentSession`` against a real
``Bridge``, watching for the canary's segments in the root ``Transcript``.

The shape is the CLI probe's; the harness is ``verify_bus_live.py``'s frame loop
-- drain, apply, settle, auto-approve -- because the sub-agent spawn parks at the
real gate here, which is part of what is being measured.

**The observable is still ordering, not state.** Timeline signals, all taken at
drain time on the frame loop:

  send-effective   the root's StateChanged(THINKING, "reading your message")
  turn-1 boundary  UsageAccrued on the root with a nonzero cost delta -- only
                   ``Translator._result`` emits one, so it uniquely marks a
                   ResultMessage being handled
  canary           first OUTPUT segment in the root Transcript carrying BANANA
                   (OUTPUT only: the follow-up prompt itself lands as SYSTEM)
  stop             AgentFinished for the sub-agent node, discounting a
                   premature stop that a later AgentResumed reopens

  ANSWERED-IN-WAIT-LOOP  canary before the final stop, no AWAITING_INPUT first
  ANSWERED-MAIN-LOOP     canary before the final stop, but AWAITING_INPUT came
                         first: the wait loop was skipped, rerun for the named
                         path
  BUFFERED               canary only after the final stop
  INCONCLUSIVE           no canary, no stop, or SLEPT contaminating the root
                         transcript (an unjoined sub-agent falls back there,
                         and then attribution is void)

Usage:  .venv/bin/python scripts/verify_lead_turn_via_agent_session.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pptmstr.bridge import Bridge, Decision  # noqa: E402
from pptmstr.driver import AgentSession  # noqa: E402
from pptmstr.intents import (  # noqa: E402
    AgentFinished,
    AgentResumed,
    AgentSpawned,
    StateChanged,
    SubagentDelivered,
    UsageAccrued,
)
from pptmstr.model import AgentState, ApprovalNeeded  # noqa: E402
from pptmstr.store import Store  # noqa: E402
from pptmstr.transcript import SegmentKind  # noqa: E402

CANARY = "BANANA"

SPAWN_PROMPT = (
    "Call the Task tool exactly once, right now, with subagent_type "
    "'general-purpose' and this prompt: 'Run the bash command `sleep 45` in the "
    "FOREGROUND and wait for it to finish -- do not use run_in_background, do not "
    "append an ampersand, do not poll. When it returns, reply with the word SLEPT "
    "and nothing else.' Launch it in the background and do NOT wait for its result "
    "-- end your turn as soon as the tool call is accepted. Do not use any pptmstr "
    "board tool. Say nothing else."
)

FOLLOWUP_PROMPT = (
    f"Reply with exactly the word {CANARY} and nothing else. "
    "Do not call any tool. Do not mention the subagent."
)

DEADLINE_S = 240.0

t0 = time.monotonic()
events: list[tuple[float, str, str]] = []


def mark(kind: str, detail: str = "") -> None:
    events.append((time.monotonic() - t0, kind, detail))


def main() -> int:
    bridge = Bridge()
    bridge.start()
    store = Store()
    session = AgentSession(bridge, task=SPAWN_PROMPT)
    session.announce()
    running = bridge.submit(session.run())
    mark("send", "spawn prompt (as opening task)")

    live_subs: set[str] = set()
    sub_live_since: float | None = None
    root_cost_at: float | None = None
    awaiting_at: float | None = None
    canary_at: float | None = None
    slept_in_root = False
    sent_followup = False
    approved: set[str] = set()
    scanned = 0

    deadline = time.monotonic() + DEADLINE_S
    try:
        while time.monotonic() < deadline:
            # The frame loop from app.py with the UI removed; order is the order
            # app.py uses. Intents are logged before apply because the verdict
            # discriminates on them -- a stop+resume applied in one batch is
            # invisible in the snapshot.
            intents = bridge.drain()
            for intent in intents:
                _log(intent, session, live_subs)
                if isinstance(intent, UsageAccrued) and intent.node_id == session.node_id:
                    if root_cost_at is None and intent.delta.total_cost_usd > 0:
                        root_cost_at = time.monotonic() - t0
                        mark("Result/root", "cost bump: turn boundary handled")
                if isinstance(intent, StateChanged) and intent.node_id == session.node_id:
                    if intent.state is AgentState.AWAITING_INPUT and awaiting_at is None:
                        awaiting_at = time.monotonic() - t0
            for effect in store.apply_all(intents):
                bridge.settle(effect)

            # The stand-in operator: everything is approved immediately. This is
            # an observation run, not a gate test -- but the park/resolve round
            # trip itself is the real path, which the CLI-layer probe bypassed.
            for obligation in store.snapshot().needs_you:
                if (
                    isinstance(obligation, ApprovalNeeded)
                    and obligation.approval.id not in approved
                ):
                    approved.add(obligation.approval.id)
                    mark("approve", obligation.summary)
                    bridge.resolve(obligation.approval.id, Decision(approved=True))

            # Scan only what was published since the last look, OUTPUT only.
            published = session.transcript.published_length
            if published > scanned:
                for seg in session.transcript.segments():
                    if seg.end <= scanned or seg.kind is not SegmentKind.OUTPUT:
                        continue
                    text = session.transcript.read(seg.start, seg.end)
                    if CANARY in text and canary_at is None:
                        canary_at = time.monotonic() - t0
                        mark("canary/root", text.strip().replace("\n", " ")[:80])
                    if "SLEPT" in text:
                        slept_in_root = True
                        mark("CONTAMINATED", "SLEPT in root OUTPUT: unjoined sub-agent fallback")
                scanned = published

            # Send once the sub-agent is live and the root's first turn has been
            # handled. send() is scheduled on the same event loop run() lives on,
            # so it can only execute while run() is parked at an await -- with
            # live sub-agents, inside _await_subagents. The verdict does not
            # trust this scheduling argument: it discriminates post hoc.
            #
            # The fallback exists because no run has ever observed a nonzero
            # total_cost_usd in this environment: if the cost bump never comes,
            # waiting on it would burn the whole run. A fallback send may land
            # mid-turn-1; the report's NOTE arm catches exactly that.
            if sub_live_since is None and live_subs:
                sub_live_since = time.monotonic()
            if not sent_followup and live_subs:
                cost_seen = root_cost_at is not None
                waited_out = sub_live_since is not None and time.monotonic() - sub_live_since > 10.0
                if cost_seen or waited_out:
                    if not cost_seen:
                        mark("send", "followup prompt (FALLBACK: no cost bump 10s after spawn)")
                    elif awaiting_at is None:
                        mark("send", "followup prompt (sub-agent live, turn 1 done)")
                    else:
                        mark("send", "followup prompt (wait loop already exited)")
                    bridge.submit(session.send(FOLLOWUP_PROMPT))
                    sent_followup = True

            if running.done():
                mark("run", "session ended")
                break
            settled_out = canary_at is not None and awaiting_at is not None
            if sent_followup and settled_out and not live_subs:
                break
            time.sleep(0.01)
    finally:
        # Asked-for teardown, so run()'s cancellation arm reports DONE rather
        # than FAILED; bridge.stop()'s loop thread does the cancelling.
        session.teardown_requested = True
        for effect in store.apply_all(bridge.drain()):
            bridge.settle(effect)
        bridge.stop()

    report(canary_at, awaiting_at, root_cost_at, slept_in_root, sent_followup)
    return 0


def _log(intent: object, session: AgentSession, live_subs: set[str]) -> None:
    if isinstance(intent, AgentSpawned) and intent.node_id != session.node_id:
        agent_id = intent.node_id[1]
        if agent_id is not None:
            live_subs.add(agent_id)
        mark("AgentSpawned/sub", str(intent.agent_type or ""))
    elif isinstance(intent, AgentFinished):
        if intent.node_id == session.node_id:
            mark("AgentFinished/root", intent.state.value)
        else:
            agent_id = intent.node_id[1]
            if agent_id is not None:
                live_subs.discard(agent_id)
            mark("AgentFinished/sub", intent.state.value)
    elif isinstance(intent, AgentResumed) and intent.node_id != session.node_id:
        agent_id = intent.node_id[1]
        if agent_id is not None:
            live_subs.add(agent_id)
        mark("AgentResumed/sub", "premature stop reopened")
    elif isinstance(intent, SubagentDelivered):
        mark("SubagentDelivered", intent.text.strip().replace("\n", " ")[:60])
    elif isinstance(intent, StateChanged) and intent.node_id == session.node_id:
        mark("state/root", f"{intent.state.value}  {intent.topic or ''}")


def report(
    canary_at: float | None,
    awaiting_at: float | None,
    root_cost_at: float | None,
    slept_in_root: bool,
    sent_followup: bool,
) -> None:
    print("\n=== timeline (seconds from start) ===")
    for at, kind, detail in events:
        print(f"  {at:7.2f}  {kind:<22} {detail}")

    stops = [at for at, k, _ in events if k == "AgentFinished/sub"]
    resumes = [at for at, k, _ in events if k == "AgentResumed/sub"]
    final_stop = stops[-1] if stops else None
    send_at = next((at for at, k, d in events if k == "send" and "followup" in d), None)

    print("\n=== verdict ===")
    if slept_in_root:
        print("  INCONCLUSIVE: SLEPT appeared in the root transcript's OUTPUT.")
        print("  An unjoined sub-agent fell back to the root; attribution is void.")
    elif not sent_followup:
        print("  INCONCLUSIVE: the follow-up was never sent -- no window opened.")
    elif canary_at is None:
        print(f"  INCONCLUSIVE: the canary {CANARY!r} never reached the root Transcript.")
    elif final_stop is None or (resumes and resumes[-1] > final_stop):
        print("  INCONCLUSIVE: the sub-agent never conclusively stopped.")
    elif canary_at < final_stop:
        if awaiting_at is None or awaiting_at > canary_at:
            print(
                f"  ANSWERED-IN-WAIT-LOOP: canary at {canary_at:.2f}s, final stop at "
                f"{final_stop:.2f}s."
            )
            print("  AgentSession forwarded a turn while inside _await_subagents.")
        else:
            print(
                f"  ANSWERED-MAIN-LOOP: canary at {canary_at:.2f}s, final stop at "
                f"{final_stop:.2f}s,"
            )
            print(f"  but AWAITING_INPUT at {awaiting_at:.2f}s preceded it: the wait loop was")
            print("  skipped (premature stop emptied _live_subagents). Evidence the turn is")
            print("  forwarded, but not through the named path -- rerun.")
    else:
        print(f"  BUFFERED: final stop at {final_stop:.2f}s, canary at {canary_at:.2f}s.")
        print("  The turn was held until the sub-agent finished.")

    if send_at is not None and root_cost_at is not None and send_at < root_cost_at:
        print(
            f"  NOTE: send at {send_at:.2f}s preceded the turn-1 boundary at "
            f"{root_cost_at:.2f}s -- the mid-turn flaw the CLI probe's run 2 had."
        )


if __name__ == "__main__":
    raise SystemExit(main())
